from gluetun_watchguard.clients.qbittorrent import QbittorrentClient
from gluetun_watchguard.config import Config


class FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def make_qbit():
    cfg = Config(client_url="http://gluetun:8080", client_username="admin", client_password="x")
    return QbittorrentClient(cfg)


def _post_returns(client, resp):
    client._session.post = lambda *a, **k: resp


def test_login_ok_200():
    c = make_qbit()
    _post_returns(c, FakeResp(200, "Ok."))
    assert c._login() is True


def test_login_204_auth_bypass_is_accepted():
    c = make_qbit()
    _post_returns(c, FakeResp(204, ""))
    assert c._login() is True


def test_login_200_fails_is_rejected():
    c = make_qbit()
    _post_returns(c, FakeResp(200, "Fails."))
    assert c._login() is False


def test_login_403_is_rejected():
    c = make_qbit()
    _post_returns(c, FakeResp(403, ""))
    assert c._login() is False
