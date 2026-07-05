from gluetun_watchguard.config import Config
from gluetun_watchguard.connectivity import (
    HEALTH_DOWN,
    HEALTH_UNKNOWN,
    HEALTH_UP,
    OutboundProbe,
)
from gluetun_watchguard.gluetun import UNKNOWN


class FakeGluetun:
    def __init__(self, ip):
        self.ip = ip

    def public_ip(self):
        return self.ip


def make_probe(proxy="", ip="1.2.3.4"):
    cfg = Config(gluetun_http_proxy=proxy)
    return OutboundProbe(cfg, FakeGluetun(ip))


def test_public_ip_present_is_up():
    assert make_probe(ip="1.2.3.4").check() == HEALTH_UP


def test_public_ip_absent_is_down():
    assert make_probe(ip=None).check() == HEALTH_DOWN


def test_public_ip_unreachable_is_unknown():
    assert make_probe(ip=UNKNOWN).check() == HEALTH_UNKNOWN


def test_proxy_success_short_circuits_over_public_ip(monkeypatch):
    # public IP would say DOWN, but a working proxy proves egress -> UP wins.
    probe = make_probe(proxy="http://gluetun:8888", ip=None)
    monkeypatch.setattr(probe, "_probe_proxy", lambda: HEALTH_UP)
    assert probe.check() == HEALTH_UP


def test_proxy_failure_falls_back_to_public_ip(monkeypatch):
    probe = make_probe(proxy="http://gluetun:8888", ip="1.2.3.4")
    monkeypatch.setattr(probe, "_probe_proxy", lambda: HEALTH_UNKNOWN)
    assert probe.check() == HEALTH_UP  # fell back to the (healthy) public IP
