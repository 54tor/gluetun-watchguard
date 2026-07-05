"""Client for the gluetun control server (https://github.com/qdm12/gluetun)."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("watchguard.gluetun")


class GluetunControl:
    """Reads the forwarded port and public IP from gluetun's HTTP control API."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        username: str = "",
        password: str = "",
        timeout: int = 10,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        if api_key:
            self._session.headers["X-API-Key"] = api_key
        self._auth = (username, password) if username else None

    def _get(self, path: str) -> dict | None:
        url = f"{self._base}{path}"
        try:
            resp = self._session.get(url, auth=self._auth, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.debug("gluetun GET %s failed: %s", path, exc)
            return None

    def forwarded_port(self) -> int | None:
        """Return the VPN-forwarded port, or None if not available yet."""
        data = self._get("/v1/openvpn/portforwarded")
        if not data:
            return None
        port = data.get("port")
        if isinstance(port, int) and port > 0:
            return port
        return None

    def public_ip(self) -> str | None:
        """Return gluetun's current public IP, or None if the tunnel is down.

        A missing public IP is our authoritative signal that the ``tun``
        interface is down / not routing.
        """
        data = self._get("/v1/publicip/ip")
        if not data:
            return None
        ip = data.get("public_ip") or data.get("ip")
        return ip or None
