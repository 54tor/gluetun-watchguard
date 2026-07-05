from gluetun_watchguard.gluetun import GluetunControl, parse_forwarded_port


def make(port_file=""):
    return GluetunControl("http://gluetun:8000", port_file=port_file)


def test_forwarded_port_from_file(tmp_path):
    f = tmp_path / "forwarded_port"
    f.write_text("51820\n")
    assert make(str(f)).forwarded_port() == 51820


def test_forwarded_port_file_missing_is_none(tmp_path):
    assert make(str(tmp_path / "nope")).forwarded_port() is None


def test_forwarded_port_file_invalid_is_none(tmp_path):
    f = tmp_path / "forwarded_port"
    f.write_text("not-a-port")
    assert make(str(f)).forwarded_port() is None


def test_forwarded_port_file_zero_is_none(tmp_path):
    f = tmp_path / "forwarded_port"
    f.write_text("0")
    assert make(str(f)).forwarded_port() is None


def test_parse_forwarded_port_valid():
    assert parse_forwarded_port("51820\n") == 51820


def test_parse_forwarded_port_invalid():
    assert parse_forwarded_port("nope") is None
    assert parse_forwarded_port("") is None
    assert parse_forwarded_port("0") is None
