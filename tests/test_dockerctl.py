import io
import tarfile

from gluetun_watchguard.dockerctl import _extract_tar_file


def _tar(name, content):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = content.encode()
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_tar_file_returns_content():
    assert _extract_tar_file(_tar("forwarded_port", "51820\n")) == b"51820\n"


def test_extract_tar_file_bad_data_is_none():
    assert _extract_tar_file(b"not a tar archive") is None


def test_parse_time_handles_trimmed_nanoseconds():
    from gluetun_watchguard.dockerctl import _parse_time

    assert _parse_time("2026-10-08T04:31:00.5Z") > _parse_time("2026-10-08T04:31:00.123456789Z")
    assert _parse_time("0001-01-01T00:00:00Z") is None


def test_dependents_match_by_id_or_name():
    from gluetun_watchguard.dockerctl import DockerSocket

    d = DockerSocket()
    d.inspect = lambda c: {"Id": "abc123" + "0" * 58, "Name": "/gluetun"}
    d._get_json = lambda path: [
        {"Id": "q", "HostConfig": {"NetworkMode": "container:abc123" + "0" * 58}},
        {"Id": "w", "HostConfig": {"NetworkMode": "container:gluetun"}},
        {"Id": "x", "HostConfig": {"NetworkMode": "bridge"}},
        {"Id": "y", "HostConfig": {"NetworkMode": "container:other"}},
    ]
    assert d.dependents("gluetun") == ["q", "w"]
