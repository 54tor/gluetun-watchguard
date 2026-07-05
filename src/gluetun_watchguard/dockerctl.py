"""Minimal Docker Engine API client over the unix socket (stdlib only).

We deliberately avoid the heavyweight ``docker`` SDK: the only operation we
need is restarting/stopping a container, which is a single POST over the socket.
"""

from __future__ import annotations

import http.client
import io
import json
import logging
import socket
import tarfile
from urllib.parse import quote

log = logging.getLogger("watchguard.docker")

_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


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

    # --- compose service resolution ---
    def _get_json(self, path: str):
        try:
            status, body = self._request("GET", path)
        except OSError as exc:
            log.error("docker socket error on %s: %s", path, exc)
            return None
        if status != 200:
            log.error("docker API %s returned %s: %s", path, status, _short(body))
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    def inspect(self, container: str) -> dict | None:
        return self._get_json(f"/containers/{container}/json")

    def detect_compose_project(self) -> str | None:
        """Read our own container's compose project label (self-detection)."""
        data = self.inspect(socket.gethostname())
        if not data:
            return None
        labels = (data.get("Config") or {}).get("Labels") or {}
        return labels.get(_COMPOSE_PROJECT_LABEL)

    def resolve_compose_service(self, service: str, project: str | None = None) -> str | None:
        """Resolve a compose service to a container id via labels.

        Returns None if the project can't be determined or nothing matches.
        More robust than a fixed name: it survives `compose up` recreating the
        container with a new generated name.
        """
        project = project or self.detect_compose_project()
        if not project:
            log.error(
                "cannot determine compose project for service %r; "
                "set COMPOSE_PROJECT or GLUETUN_CONTAINER",
                service,
            )
            return None
        labels = [
            f"{_COMPOSE_PROJECT_LABEL}={project}",
            f"{_COMPOSE_SERVICE_LABEL}={service}",
        ]
        filters = json.dumps({"label": labels})
        containers = self._get_json(f"/containers/json?all=true&filters={quote(filters)}")
        if not containers:
            log.error("no container found for compose service %r in project %r", service, project)
            return None
        # Prefer a running match if several exist.
        running = [c for c in containers if c.get("State") == "running"]
        chosen = (running or containers)[0]
        return chosen.get("Id") or ((chosen.get("Names") or [None])[0])

    def read_file(self, container: str, path: str) -> bytes | None:
        """Read a file from a container via the archive endpoint.

        Uses the Docker socket we already hold, so no volume mount is needed.
        """
        try:
            status, body = self._request(
                "GET", f"/containers/{container}/archive?path={quote(path)}"
            )
        except OSError as exc:
            log.error("docker archive read %s:%s failed: %s", container, path, exc)
            return None
        if status != 200:
            log.debug("docker archive %s:%s -> HTTP %s", container, path, status)
            return None
        return _extract_tar_file(body)


def _short(body: bytes, limit: int = 200) -> str:
    return body.decode("utf-8", "replace").strip()[:limit]


def _extract_tar_file(body: bytes) -> bytes | None:
    """Return the content of the first regular file in a tar archive."""
    try:
        with tarfile.open(fileobj=io.BytesIO(body)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    handle = tar.extractfile(member)
                    return handle.read() if handle else None
    except (tarfile.TarError, OSError):
        return None
    return None
