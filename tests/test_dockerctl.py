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
