import pytest

from gluetun_watchguard.config import Config

_KEYS = (
    "TORRENT_CLIENT",
    "CHECK_INTERVAL",
    "GLUETUN_CONTROL_URL",
    "ENABLE_DOCKER_ACTION",
    "DOCKER_ACTION",
)


def _clear(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch):
    _clear(monkeypatch)
    cfg = Config.from_env()
    assert cfg.client_kind == "qbittorrent"
    assert cfg.check_interval == 30
    assert cfg.gluetun_url == "http://gluetun:8000"
    assert cfg.docker_action == "restart"


def test_bool_and_int_parsing(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ENABLE_DOCKER_ACTION", "false")
    monkeypatch.setenv("CHECK_INTERVAL", "15")
    cfg = Config.from_env()
    assert cfg.enable_docker_action is False
    assert cfg.check_interval == 15


def test_rejects_unknown_client(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TORRENT_CLIENT", "deluge")
    with pytest.raises(ValueError):
        Config.from_env()


def test_rejects_unknown_action(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_ACTION", "delete")
    with pytest.raises(ValueError):
        Config.from_env()
