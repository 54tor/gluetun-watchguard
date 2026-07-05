"""Client for the gluetun control server (https://github.com/qdm12/gluetun)."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("watchguard.gluetun")


class _Unknown:
    """Sentinel: the control server could not be reached / did not answer.

    Distinct from a definitive negative (a valid response carrying no IP), so a
    slow or unreachable control server is never mistaken for "tunnel down".
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()


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

    def _get(self, path: str) -> dict | _Unknown:
        """Return parsed JSON on success, or ``UNKNOWN`` on transport/HTTP error.

        A timeout or connection error is *not* a definitive answer, so callers
        must not treat it as "down".
        """
        url = f"{self._base}{path}"
        try:
            resp = self._session.get(url, auth=self._auth, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.debug("gluetun GET %s failed: %s", path, exc)
            return UNKNOWN

    def forwarded_port(self) -> int | None:
        """Return the VPN-forwarded port, or None if not available yet."""
        data = self._get("/v1/openvpn/portforwarded")
        if data is UNKNOWN or not data:
            return None
        port = data.get("port")
        if isinstance(port, int) and port > 0:
            return port
        return None

    def public_ip(self) -> str | _Unknown | None:
        """Return gluetun's public IP, tri-state.

        * a non-empty string ⇒ tunnel up and routing;
        * ``None`` ⇒ the control server answered but carries no IP (tunnel down);
        * ``UNKNOWN`` ⇒ the control server was slow / unreachable — draw no
          conclusion (a slow response must never trigger a restart).
        """
        data = self._get("/v1/publicip/ip")
        if data is UNKNOWN:
            return UNKNOWN
        if not data:
            return None
        ip = data.get("public_ip") or data.get("ip")
        return ip or None


def build_gluetun(cfg) -> GluetunControl:
    return GluetunControl(
        cfg.gluetun_url,
        api_key=cfg.gluetun_api_key,
        username=cfg.gluetun_username,
        password=cfg.gluetun_password,
        timeout=cfg.http_timeout,
    )
