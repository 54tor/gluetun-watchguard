from gluetun_watchguard.config import Config
from gluetun_watchguard.debounce import FailureTracker
from gluetun_watchguard.watchdog import Watchdog


class FakeGluetun:
    def __init__(self, port=None, ip="1.2.3.4"):
        self.port = port
        self.ip = ip
        self.ip_calls = 0

    def forwarded_port(self):
        return self.port

    def public_ip(self):
        self.ip_calls += 1
        return self.ip


class FakeClient:
    def __init__(self, port=6881, conn=None):
        self.port = port
        self.conn = conn
        self.set_calls = []

    def get_listen_port(self):
        return self.port

    def set_listen_port(self, port):
        self.set_calls.append(port)
        self.port = port
        return True

    def connection_ok(self):
        return self.conn


class FakeDocker:
    def __init__(self):
        self.restarts = []

    def restart(self, container, **_):
        self.restarts.append(container)
        return True

    def stop(self, container, **_):
        self.restarts.append(container)
        return True


def make_watchdog(cfg=None, gluetun=None, client=None, docker=None):
    cfg = cfg or Config(startup_grace=0, failure_threshold=2, restart_cooldown=0)
    wd = Watchdog(cfg)  # constructors do no network I/O
    wd.gluetun = gluetun or FakeGluetun()
    wd.client = client or FakeClient()
    wd.docker = docker or FakeDocker()
    wd.tracker = FailureTracker(cfg.failure_threshold, cfg.restart_cooldown, cfg.startup_grace)
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
