from gluetun_watchguard.config import Config
from gluetun_watchguard.connectivity import OutboundProbe
from gluetun_watchguard.debounce import FailureTracker
from gluetun_watchguard.gluetun import UNKNOWN
from gluetun_watchguard.watchdog import Watchdog, _short_id


class FakeGluetun:
    def __init__(self, port=None, ip="1.2.3.4"):
        self.port = port
        self.ip = ip
        self.ip_calls = 0
        self.port_calls = 0

    def forwarded_port(self):
        self.port_calls += 1
        return self.port

    def public_ip(self):
        self.ip_calls += 1
        return self.ip


class FakeClient:
    def __init__(self, port=6881, conn=None, open=None):
        self.port = port
        self.conn = conn
        self.open = open
        self.set_calls = []

    def get_listen_port(self):
        return self.port

    def set_listen_port(self, port):
        self.set_calls.append(port)
        self.port = port
        return True

    def connection_ok(self):
        return self.conn

    def port_is_open(self):
        return self.open


class FakeDocker:
    def __init__(self, resolved="proj-gluetun-1", file=None, running=True, health=None):
        self.restarts = []
        self.stops = []
        self.starts = []
        self.resolved = resolved
        self.resolve_calls = []
        self.file = file
        self.read_calls = []
        self.running = running
        self.health = health
        self.state_calls = 0

    def restart(self, container, **_):
        self.restarts.append(container)
        return True

    def stop(self, container, **_):
        self.stops.append(container)
        return True

    def start(self, container, **_):
        self.starts.append(container)
        return True

    def resolve_compose_service(self, service, project=None):
        self.resolve_calls.append((service, project))
        return self.resolved

    def read_file(self, container, path):
        self.read_calls.append((container, path))
        return self.file

    def container_state(self, container):
        self.state_calls += 1
        return self.running, self.health


def make_watchdog(cfg=None, gluetun=None, client=None, docker=None):
    cfg = cfg or Config(startup_grace=0, failure_threshold=2, restart_cooldown=0)
    wd = Watchdog(cfg)  # constructors do no network I/O
    wd.gluetun = gluetun or FakeGluetun()
    wd.client = client or FakeClient()
    wd.docker = docker or FakeDocker()
    # Rebuild the probe against the fake gluetun (no proxy => uses public IP).
    wd.probe = OutboundProbe(cfg, wd.gluetun)
    wd.tunnel_tracker = FailureTracker(
        cfg.failure_threshold, cfg.restart_cooldown, cfg.startup_grace
    )
    wd.port_tracker = FailureTracker(
        cfg.failure_threshold, cfg.restart_cooldown, cfg.startup_grace
    )
    return wd


def test_sync_port_updates_when_different():
    wd = make_watchdog(gluetun=FakeGluetun(port=55000), client=FakeClient(port=6881, conn=True))
    wd.sync_port()
    assert wd.client.set_calls == [55000]


def test_sync_port_noop_when_equal():
    wd = make_watchdog(gluetun=FakeGluetun(port=6881), client=FakeClient(port=6881, conn=True))
    wd.sync_port()
    assert wd.client.set_calls == []


def test_health_ok_short_circuits_on_client():
    g = FakeGluetun(ip="1.2.3.4")
    wd = make_watchdog(gluetun=g, client=FakeClient(conn=True))
    wd.check_health()
    assert g.ip_calls == 0  # client reported OK, no need to probe gluetun
    assert wd.docker.restarts == []


def test_health_ok_via_gluetun_when_client_unknown():
    g = FakeGluetun(ip="1.2.3.4")
    wd = make_watchdog(gluetun=g, client=FakeClient(conn=None))
    wd.check_health()
    assert g.ip_calls == 1
    assert wd.docker.restarts == []


def test_restart_after_sustained_tunnel_loss():
    cfg = Config(
        startup_grace=0, failure_threshold=2, restart_cooldown=0, gluetun_container="gluetun"
    )
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=None), client=FakeClient(conn=False), docker=d
    )
    wd.check_health()  # failure 1
    assert d.restarts == []
    wd.check_health()  # failure 2 -> act
    assert d.restarts == ["gluetun"]


def test_unknown_control_server_never_acts_and_holds_counter():
    cfg = Config(startup_grace=0, failure_threshold=1, restart_cooldown=0)
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=UNKNOWN), client=FakeClient(conn=None), docker=d
    )
    for _ in range(5):
        wd.check_health()  # slow/unreachable control server, every tick
    assert d.restarts == []
    assert wd.tunnel_tracker.consecutive == 0  # latency must not count as a failure


def test_slow_client_with_healthy_tunnel_stays_up():
    # client times out (connection_ok -> None) but gluetun still has a public IP
    cfg = Config(startup_grace=0, failure_threshold=1, restart_cooldown=0)
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip="1.2.3.4"), client=FakeClient(conn=None), docker=d
    )
    wd.check_health()
    assert d.restarts == []
    assert wd.tunnel_tracker.consecutive == 0


def test_no_restart_when_action_disabled():
    cfg = Config(
        startup_grace=0, failure_threshold=1, restart_cooldown=0, enable_docker_action=False
    )
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=None), client=FakeClient(conn=False), docker=d
    )
    wd.check_health()
    assert d.restarts == []


def test_port_check_warns_but_never_acts_when_recovery_disabled():
    cfg = Config(
        startup_grace=0, failure_threshold=1, restart_cooldown=0, port_check_recovery=False
    )
    d = FakeDocker()
    wd = make_watchdog(cfg=cfg, client=FakeClient(open=False), docker=d)
    wd.check_port()
    assert d.restarts == []


def test_port_check_recovers_when_enabled_and_sustained():
    cfg = Config(
        startup_grace=0,
        failure_threshold=2,
        restart_cooldown=0,
        port_check_recovery=True,
        gluetun_container="gluetun",
    )
    d = FakeDocker()
    wd = make_watchdog(cfg=cfg, client=FakeClient(open=False), docker=d)
    wd.check_port()  # closed 1/2
    assert d.restarts == []
    wd.check_port()  # closed 2/2 -> act
    assert d.restarts == ["gluetun"]


def test_port_check_noop_when_client_cannot_tell():
    cfg = Config(startup_grace=0, failure_threshold=1, restart_cooldown=0, port_check_recovery=True)
    d = FakeDocker()
    wd = make_watchdog(cfg=cfg, client=FakeClient(open=None), docker=d)
    wd.check_port()
    assert d.restarts == []


def test_resolve_target_prefers_explicit_container():
    cfg = Config(gluetun_container="gluetun", gluetun_service="svc")
    wd = make_watchdog(cfg=cfg, docker=FakeDocker())
    assert wd._resolve_target() == "gluetun"
    assert wd.docker.resolve_calls == []  # never touched the API


def test_resolve_target_uses_compose_service():
    cfg = Config(gluetun_container="", gluetun_service="gluetun", compose_project="proj")
    d = FakeDocker(resolved="proj-gluetun-1")
    wd = make_watchdog(cfg=cfg, docker=d)
    assert wd._resolve_target() == "proj-gluetun-1"
    assert d.resolve_calls == [("gluetun", "proj")]


def test_resolve_target_falls_back_to_default_name():
    wd = make_watchdog(cfg=Config(), docker=FakeDocker())
    assert wd._resolve_target() == "gluetun"


def test_recover_aborts_when_service_unresolved():
    cfg = Config(
        gluetun_container="",
        gluetun_service="gluetun",
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=0,
    )
    d = FakeDocker(resolved=None)  # resolution fails
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=None), client=FakeClient(conn=False), docker=d
    )
    wd.check_health()  # tunnel down -> recovery attempted -> resolve fails -> no restart
    assert d.restarts == []


def test_wanted_port_reads_from_container_when_no_volume(tmp_path):
    missing = str(tmp_path / "forwarded_port")  # not present locally
    cfg = Config(gluetun_port_file=missing, gluetun_container="gluetun")
    d = FakeDocker(file=b"48291\n")
    wd = make_watchdog(cfg=cfg, gluetun=FakeGluetun(port=None), docker=d)
    assert wd._wanted_port() == 48291
    assert d.read_calls == [("gluetun", missing)]
    assert wd.gluetun.port_calls == 0  # socket mode skips the local read entirely


def test_wanted_port_prefers_local_file_over_socket(tmp_path):
    f = tmp_path / "forwarded_port"
    f.write_text("6881")
    cfg = Config(gluetun_port_file=str(f))
    d = FakeDocker(file=b"9999")
    # FakeGluetun returns the port as the real client would after reading the file.
    wd = make_watchdog(cfg=cfg, gluetun=FakeGluetun(port=6881), docker=d)
    assert wd._wanted_port() == 6881
    assert d.read_calls == []


def test_short_id_shortens_full_container_id():
    assert _short_id("d" * 64) == "d" * 12


def test_short_id_keeps_names_and_short_refs():
    assert _short_id("gluetun") == "gluetun"
    assert _short_id("myproject-gluetun-1") == "myproject-gluetun-1"


def test_wanted_port_api_only_when_no_file():
    wd = make_watchdog(cfg=Config(), gluetun=FakeGluetun(port=55000), docker=FakeDocker())
    assert wd._wanted_port() == 55000
    assert wd.docker.read_calls == []


def test_shared_cooldown_prevents_double_restart():
    cfg = Config(
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=999,
        port_check_recovery=True,
        gluetun_container="gluetun",
    )
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg,
        gluetun=FakeGluetun(ip=None),
        client=FakeClient(conn=False, open=False),
        docker=d,
    )
    wd.check_health()  # tunnel down -> restart
    assert d.restarts == ["gluetun"]
    wd.check_port()  # port closed too, but shared cooldown blocks a second restart
    assert d.restarts == ["gluetun"]


# --- G1: container-health escalation on UNKNOWN ---


def test_unknown_escalates_to_recovery_when_container_exited():
    cfg = Config(
        startup_grace=0, failure_threshold=1, restart_cooldown=0, gluetun_container="gluetun"
    )
    d = FakeDocker(running=False)  # container exited
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=UNKNOWN), client=FakeClient(conn=None), docker=d
    )
    wd.check_health()  # UNKNOWN + dead container -> DOWN -> restart
    assert d.restarts == ["gluetun"]


def test_unknown_escalates_when_container_unhealthy():
    cfg = Config(
        startup_grace=0, failure_threshold=1, restart_cooldown=0, gluetun_container="gluetun"
    )
    d = FakeDocker(running=True, health="unhealthy")
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=UNKNOWN), client=FakeClient(conn=None), docker=d
    )
    wd.check_health()
    assert d.restarts == ["gluetun"]


def test_unknown_stays_unknown_when_container_alive():
    cfg = Config(
        startup_grace=0, failure_threshold=1, restart_cooldown=0, gluetun_container="gluetun"
    )
    d = FakeDocker(running=True, health=None)
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=UNKNOWN), client=FakeClient(conn=None), docker=d
    )
    wd.check_health()
    assert d.restarts == []
    assert wd.tunnel_tracker.consecutive == 0


def test_container_health_toggle_off_skips_inspection():
    cfg = Config(
        enable_container_health=False,
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=0,
        gluetun_container="gluetun",
    )
    d = FakeDocker(running=False)
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=UNKNOWN), client=FakeClient(conn=None), docker=d
    )
    wd.check_health()
    assert d.restarts == []
    assert d.state_calls == 0


# --- G2: HEALTH_REQUIRE_EGRESS ---


def test_health_require_egress_ignores_connected_client():
    cfg = Config(
        health_require_egress=True,
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=0,
        gluetun_container="gluetun",
    )
    d = FakeDocker()
    g = FakeGluetun(ip=None)  # egress is actually down
    wd = make_watchdog(cfg=cfg, gluetun=g, client=FakeClient(conn=True), docker=d)
    wd.check_health()  # client 'connected' ignored; probe says DOWN -> restart
    assert d.restarts == ["gluetun"]
    assert g.ip_calls == 1  # egress was probed


def test_default_trusts_connected_client_fastpath():
    cfg = Config(startup_grace=0, failure_threshold=1, restart_cooldown=0)
    g = FakeGluetun(ip=None)
    d = FakeDocker()
    wd = make_watchdog(cfg=cfg, gluetun=g, client=FakeClient(conn=True), docker=d)
    wd.check_health()
    assert d.restarts == []
    assert g.ip_calls == 0  # fast-path, egress not probed


# --- Recovery cycle (kill-switch) ---


def test_recovery_cycles_client_stop_restart_await_start():
    cfg = Config(
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=0,
        gluetun_container="gluetun",
        client_container="qbit",
        recovery_healthy_timeout=30,
    )
    d = FakeDocker()
    g = FakeGluetun(ip=None)  # tunnel down
    wd = make_watchdog(cfg=cfg, gluetun=g, client=FakeClient(conn=False), docker=d)
    now = [1000.0]
    wd._clock = lambda: now[0]

    wd.check_health()  # DOWN -> start the recovery cycle
    assert d.stops == ["qbit"]
    assert d.restarts == ["gluetun"]
    assert d.starts == []
    assert wd._recovery_until == 1030.0

    now[0] = 1010.0
    wd.tick()  # still down, before deadline -> keep waiting
    assert d.starts == []

    g.ip = "1.2.3.4"  # gluetun healthy again
    now[0] = 1015.0
    wd.tick()
    assert d.starts == ["qbit"]
    assert wd._recovery_until is None


def test_recovery_starts_client_after_timeout():
    cfg = Config(
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=0,
        gluetun_container="gluetun",
        client_container="qbit",
        recovery_healthy_timeout=30,
    )
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=None), client=FakeClient(conn=False), docker=d
    )
    now = [1000.0]
    wd._clock = lambda: now[0]
    wd.check_health()  # recovery, until=1030
    now[0] = 1031.0
    wd.tick()  # past deadline, still down -> start client anyway
    assert d.starts == ["qbit"]
    assert wd._recovery_until is None


def test_recovery_without_client_just_restarts_gluetun():
    cfg = Config(
        startup_grace=0, failure_threshold=1, restart_cooldown=0, gluetun_container="gluetun"
    )
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=None), client=FakeClient(conn=False), docker=d
    )
    wd.check_health()
    assert d.restarts == ["gluetun"]
    assert d.stops == []
    assert d.starts == []
    assert wd._recovery_until is None


def test_recovery_stop_action_is_killswitch():
    cfg = Config(
        startup_grace=0,
        failure_threshold=1,
        restart_cooldown=0,
        docker_action="stop",
        gluetun_container="gluetun",
        client_container="qbit",
    )
    d = FakeDocker()
    wd = make_watchdog(
        cfg=cfg, gluetun=FakeGluetun(ip=None), client=FakeClient(conn=False), docker=d
    )
    wd.check_health()
    assert d.stops == ["qbit", "gluetun"]
    assert d.restarts == []
    assert d.starts == []
    assert wd._recovery_until is None
