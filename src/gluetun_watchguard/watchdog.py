"""Main watch loop: port sync + tunnel health + guarded recovery action."""

from __future__ import annotations

import logging
import signal
import threading

from .clients.base import build_client
from .config import Config
from .debounce import FailureTracker
from .dockerctl import DockerSocket
from .gluetun import GluetunControl

log = logging.getLogger("watchguard")


class Watchdog:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.gluetun = GluetunControl(
            cfg.gluetun_url,
            api_key=cfg.gluetun_api_key,
            username=cfg.gluetun_username,
            password=cfg.gluetun_password,
            timeout=cfg.http_timeout,
        )
        self.client = build_client(cfg)
        self.docker = DockerSocket(cfg.docker_socket, timeout=max(30, cfg.http_timeout))
        # One gate per failure mode; both share the cooldown via _recover() so a
        # single restart never chains into a second one.
        self.tunnel_tracker = FailureTracker(
            cfg.failure_threshold, cfg.restart_cooldown, cfg.startup_grace
        )
        self.port_tracker = FailureTracker(
            cfg.failure_threshold, cfg.restart_cooldown, cfg.startup_grace
        )
        self._stop = threading.Event()

    # --- lifecycle ---
    def run(self) -> None:
        self._install_signal_handlers()
        log.info(
            "watching: client=%s port_sync=%s healthcheck=%s port_check=%s(recovery=%s) "
            "docker_action=%s(%s) interval=%ss",
            self.cfg.client_kind,
            self.cfg.enable_port_sync,
            self.cfg.enable_healthcheck,
            self.cfg.enable_port_check,
            self.cfg.port_check_recovery,
            self.cfg.enable_docker_action,
            self.cfg.docker_action,
            self.cfg.check_interval,
        )
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let a transient error kill the loop
                log.exception("unexpected error during tick")
            self._stop.wait(self.cfg.check_interval)
        log.info("stopped")

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self._stop.set())
            except ValueError:  # pragma: no cover - not running on main thread
                pass

    # --- per-tick work ---
    def tick(self) -> None:
        if self.cfg.enable_port_sync:
            self.sync_port()
        if self.cfg.enable_healthcheck:
            self.check_health()
        if self.cfg.enable_port_check:
            self.check_port()

    def sync_port(self) -> None:
        wanted = self.gluetun.forwarded_port()
        if not wanted:
            log.debug("no forwarded port advertised by gluetun yet")
            return
        current = self.client.get_listen_port()
        if current is None:
            log.debug("could not read current port from %s", self.cfg.client_kind)
            return
        if current == wanted:
            return
        if self.client.set_listen_port(wanted):
            log.info("port synced: %s -> %s", current, wanted)
        else:
            log.warning("failed to set port %s on %s", wanted, self.cfg.client_kind)

    def check_health(self) -> None:
        if self.assess_health():
            if self.tunnel_tracker.consecutive:
                log.info("tunnel health recovered")
            self.tunnel_tracker.record_success()
            return
        self.tunnel_tracker.record_failure()
        log.warning(
            "tunnel health check failed (%d/%d)",
            self.tunnel_tracker.consecutive,
            self.tunnel_tracker.threshold,
        )
        if self.tunnel_tracker.should_act():
            self._recover("tun interface down")

    def check_port(self) -> None:
        """Warn (and optionally recover) when the forwarded port is not open.

        A closed forwarded port usually means the VPN provider dropped the port
        mapping, not that the tunnel is down — so recovery here is opt-in
        (`PORT_CHECK_RECOVERY`) and shares the tunnel's cooldown gate.
        """
        is_open = self.client.port_is_open()
        if is_open is None:
            return  # client can't tell; draw no conclusion
        if is_open:
            self.port_tracker.record_success()
            return
        log.warning("forwarded port appears closed / not reachable from outside")
        if not self.cfg.port_check_recovery:
            return
        self.port_tracker.record_failure()
        log.warning(
            "closed-port failures (%d/%d)",
            self.port_tracker.consecutive,
            self.port_tracker.threshold,
        )
        if self.port_tracker.should_act():
            self._recover("forwarded port closed")

    def assess_health(self) -> bool:
        """Return True when the tunnel is up.

        Escalation order (cheap first): ask the torrent client whether it sees
        connectivity, and only if that is negative/unknown do we consult the
        authoritative signal — gluetun's public IP (tun up and routing).
        """
        client_status = self.client.connection_ok()
        if client_status is True:
            return True
        ip = self.gluetun.public_ip()
        if ip is not None:
            if client_status is False:
                log.info(
                    "client reports no connectivity but tunnel is up (ip=%s); "
                    "not a tun failure, leaving gluetun alone",
                    ip,
                )
            return True
        log.warning("gluetun has no public IP: tun interface appears down")
        return False

    def _resolve_target(self) -> str | None:
        """Resolve which container to act on.

        Precedence: an explicit ``GLUETUN_CONTAINER`` wins; otherwise resolve
        ``GLUETUN_SERVICE`` through the compose labels; otherwise fall back to
        the conventional ``gluetun`` name.
        """
        if self.cfg.gluetun_container:
            return self.cfg.gluetun_container
        if self.cfg.gluetun_service:
            return self.docker.resolve_compose_service(
                self.cfg.gluetun_service, self.cfg.compose_project or None
            )
        return "gluetun"

    def _recover(self, reason: str) -> None:
        action = self.cfg.docker_action
        if not self.cfg.enable_docker_action or action == "none":
            log.error(
                "recovery needed (%s) but docker action is disabled "
                "(manual intervention required)",
                reason,
            )
            return
        target = self._resolve_target()
        if not target:
            log.error("recovery needed (%s) but target container unresolved", reason)
            return
        log.warning("recovery: %s gluetun container %r (%s)", action, target, reason)
        ok = self.docker.stop(target) if action == "stop" else self.docker.restart(target)
        if ok:
            log.info("recovery action succeeded; entering cooldown")
            # A single restart addresses both failure modes: reset both gates so
            # we never chain a second restart right after the first.
            self.tunnel_tracker.mark_action()
            self.port_tracker.mark_action()
        else:
            log.error("recovery action failed; will retry after further failures")
