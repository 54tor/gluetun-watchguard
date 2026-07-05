"""Minimal Docker Engine API client over the unix socket (stdlib only).

We deliberately avoid the heavyweight ``docker`` SDK: the only operation we
need is restarting/stopping a container, which is a single POST over the socket.
"""

from __future__ import annotations

import http.client
import logging
import socket

log = logging.getLogger("watchguard.docker")


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: int) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerSocket:
    def __init__(self, socket_path: str = "/var/run/docker.sock", timeout: int = 30) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    def _request(self, method: str, path: str) -> tuple[int, bytes]:
        conn = _UnixHTTPConnection(self._socket_path, self._timeout)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, body
        finally:
            conn.close()

    def restart(self, container: str, *, timeout_seconds: int = 30) -> bool:
        """Restart a container by name or id. Returns True on success."""
        return self._act(f"/containers/{container}/restart?t={timeout_seconds}")

    def stop(self, container: str, *, timeout_seconds: int = 30) -> bool:
        """Stop a container by name or id. Returns True on success."""
        return self._act(f"/containers/{container}/stop?t={timeout_seconds}")

    def _act(self, path: str) -> bool:
        try:
            status, body = self._request("POST", path)
        except OSError as exc:
            log.error("docker socket error on %s: %s", path, exc)
            return False
        if status in (204, 304):
            # 204 = done; 304 = already in the desired state.
            return True
        log.error("docker API %s returned %s: %s", path, status, _short(body))
        return False


def _short(body: bytes, limit: int = 200) -> str:
    return body.decode("utf-8", "replace").strip()[:limit]
