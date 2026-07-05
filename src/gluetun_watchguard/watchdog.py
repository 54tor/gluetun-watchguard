"""Main watch loop: port sync + tunnel health + guarded recovery action."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

from .clients.base import build_client
from .config import Config
from .connectivity import HEALTH_DOWN, HEALTH_UNKNOWN, HEALTH_UP, OutboundProbe
from .debounce import FailureTracker
from .dockerctl import DockerSocket
from .gluetun import build_gluetun, parse_forwarded_port

log = logging.getLogger("watchguard")

# While waiting for gluetun to come back healthy, poll faster than the normal
# interval so the client is brought back promptly.
_RECOVERY_POLL = 5


def _short_id(ref: str) -> str:
    """Shorten a full 64-hex container id to Docker's 12-char short form."""
    if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
        return ref[:12]
    return ref


class Watchdog:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.gluetun = build_gluetun(cfg)
        self.probe = OutboundProbe(cfg, self.gluetun)
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
        self._clock = time.monotonic
        # When set, we're mid-recovery: the client is stopped and we're waiting
        # for gluetun to be healthy again before starting it back.
        self._recovery_until: float | None = None
        self._pending_client: str | None = None

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
            recovering = self._recovery_until is not None
            self._stop.wait(_RECOVERY_POLL if recovering else self.cfg.check_interval)
        log.info("stopped")

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self._stop.set())
            except ValueError:  # pragma: no cover - not running on main thread
                pass

    # --- per-tick work ---
    def tick(self) -> None:
        if self._recovery_until is not None:
            self._advance_recovery()
            return
        if self.cfg.enable_port_sync:
            self.sync_port()
        if self.cfg.enable_healthcheck:
            self.check_health()
        if self.cfg.enable_port_check:
            self.check_port()

    def sync_port(self) -> None:
        wanted = self._wanted_port()
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

    def _wanted_port(self) -> int | None:
        pf = self.cfg.gluetun_port_file
        if not pf:
            return self.gluetun.forwarded_port()  # control API
        if os.path.exists(pf):
            return self.gluetun.forwarded_port()  # local file (mounted volume)
        # No volume: read the file straight from the gluetun container over the
        # Docker socket we already hold (avoids a doomed local open + noisy log).
        return self._port_from_container(pf)

    def _port_from_container(self, path: str) -> int | None:
        target = self._resolve_target()
        if not target:
            return None
        data = self.docker.read_file(target, path)
        if data is None:
            return None
        port = parse_forwarded_port(data.decode("utf-8", "replace"))
        if port is not None:
            log.debug(
                "forwarded port %d read from %s:%s via the docker socket",
                port,
                _short_id(target),
                path,
            )
        return port

    def check_health(self) -> None:
        state = self.assess_health()
        if state == HEALTH_UNKNOWN and self._gluetun_is_dead():
            # Control server & proxy both unreachable AND the container itself is
            # exited/unhealthy: a real outage, not mere latency.
            log.warning("gluetun unreachable and its container is exited/unhealthy")
            state = HEALTH_DOWN
        if state == HEALTH_UP:
            if self.tunnel_tracker.consecutive:
                log.info("tunnel health recovered")
            self.tunnel_tracker.record_success()
            return
        if state == HEALTH_UNKNOWN:
            # Slow or unreachable control server: draw no conclusion. Record
            # neither success nor failure so latency alone can never act.
            log.warning(
                "tunnel health unknown (control server unreachable, slow or unauthorized); "
                "not acting"
            )
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

    def assess_health(self) -> str:
        """Classify gluetun's outbound connectivity as UP / DOWN / UNKNOWN.

        Cheap first: if the torrent client reports it is connected, treat as up
        (unless HEALTH_REQUIRE_EGRESS forces the egress probe every time).
        Otherwise run the shared outbound probe (a request through gluetun's HTTP
        proxy, falling back to the control server's public IP). A slow or
        unreachable probe yields UNKNOWN and is never treated as down.
        """
        client_status = self.client.connection_ok()
        if client_status is True and not self.cfg.health_require_egress:
            return HEALTH_UP
        state = self.probe.check()
        if state == HEALTH_UP and client_status is False:
            log.info(
                "client reports no connectivity but gluetun outbound is up; "
                "not a tun failure, leaving gluetun alone"
            )
        elif state == HEALTH_DOWN:
            log.warning("gluetun has no outbound connectivity: tun interface appears down")
        return state

    def _gluetun_is_dead(self) -> bool:
        """True only when the gluetun container is definitively bad (exited/unhealthy).

        Escalates an ambiguous UNKNOWN into an actionable DOWN so a hung or
        crashed gluetun — which never answers the control server or proxy — is
        still recovered instead of being written off as latency.
        """
        if not self.cfg.enable_container_health:
            return False
        target = self._resolve_target()
        if not target:
            return False
        running, health = self.docker.container_state(target)
        return running is False or health == "unhealthy"

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

    def _resolve_client(self) -> str | None:
        """Resolve the torrent client container to cycle, or None if unset."""
        if self.cfg.client_container:
            return self.cfg.client_container
        if self.cfg.client_service:
            return self.docker.resolve_compose_service(
                self.cfg.client_service, self.cfg.compose_project or None
            )
        return None

    def _recover(self, reason: str) -> None:
        action = self.cfg.docker_action
        if not self.cfg.enable_docker_action or action == "none":
            log.error(
                "recovery needed (%s) but docker action is disabled "
                "(manual intervention required)",
                reason,
            )
            return
        gluetun = self._resolve_target()
        if not gluetun:
            log.error("recovery needed (%s) but gluetun container unresolved", reason)
            return
        client = self._resolve_client()

        if action == "stop":
            # Kill-switch only: stop the client then gluetun, no restart/wait.
            if client:
                self.docker.stop(client)
            if self.docker.stop(gluetun):
                log.info("recovery: stopped gluetun %r (%s)", gluetun, reason)
                self._mark_recovered()
            else:
                log.error("recovery: failed to stop gluetun %r", gluetun)
            return

        if client:
            # Orchestrated cycle: stop client -> restart gluetun -> await healthy
            # -> start client (the wait is handled across ticks).
            log.warning(
                "recovery: stop client %r, restart gluetun %r, await healthy (%s)",
                client,
                gluetun,
                reason,
            )
            self.docker.stop(client)
            if self.docker.restart(gluetun):
                self._pending_client = client
                self._recovery_until = self._clock() + self.cfg.recovery_healthy_timeout
                self._mark_recovered()
            else:
                log.error("recovery: gluetun restart failed; starting client %r back", client)
                self.docker.start(client)
            return

        log.warning("recovery: restart gluetun container %r (%s)", gluetun, reason)
        if self.docker.restart(gluetun):
            log.info("recovery action succeeded; entering cooldown")
            self._mark_recovered()
        else:
            log.error("recovery action failed; will retry after further failures")

    def _mark_recovered(self) -> None:
        # A single restart addresses both failure modes: reset both gates so we
        # never chain a second restart right after the first.
        self.tunnel_tracker.mark_action()
        self.port_tracker.mark_action()

    def _start_client(self) -> None:
        client, self._pending_client = self._pending_client, None
        if not client:
            return
        if self.docker.start(client):
            log.info("recovery complete: client %r started", client)
        else:
            log.error("recovery: failed to start client %r", client)

    def _advance_recovery(self) -> None:
        """Between ticks: wait for gluetun to be healthy, then start the client."""
        if self.assess_health() == HEALTH_UP:
            log.info("gluetun healthy again after recovery")
            self._recovery_until = None
            self._start_client()
        elif self._clock() >= (self._recovery_until or 0):
            log.warning(
                "gluetun not healthy within %ds; starting the client anyway",
                self.cfg.recovery_healthy_timeout,
            )
            self._recovery_until = None
            self._start_client()
        else:
            log.debug("recovery: awaiting gluetun healthy before starting the client")
