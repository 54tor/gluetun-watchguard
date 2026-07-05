"""qBittorrent WebUI adapter (API v2)."""

from __future__ import annotations

import json
import logging

import requests

from .base import TorrentClient

log = logging.getLogger("watchguard.client.qbittorrent")


class QbittorrentClient(TorrentClient):
    kind = "qbittorrent"

    def __init__(self, cfg) -> None:
        self._base = cfg.client_url.rstrip("/")
        self._username = cfg.client_username
        self._password = cfg.client_password
        self._timeout = cfg.http_timeout
        self._session = requests.Session()
        # qBittorrent's WebUI enforces a matching Referer for CSRF protection.
        self._session.headers["Referer"] = self._base
        self._logged_in = False

    # --- auth ---
    def _login(self) -> bool:
        if self._logged_in:
            return True
        try:
            resp = self._session.post(
                f"{self._base}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            log.debug("qbittorrent login failed: %s", exc)
            return False
        if resp.status_code == 200 and resp.text.strip() == "Ok.":
            self._logged_in = True
            return True
        log.debug("qbittorrent login rejected: %s %s", resp.status_code, resp.text[:80])
        return False

    def _api(self, method: str, path: str, **kwargs) -> requests.Response | None:
        """Call an authenticated endpoint, re-logging in once on a 403."""
        if not self._login():
            return None
        url = f"{self._base}{path}"
        try:
            resp = self._session.request(method, url, timeout=self._timeout, **kwargs)
            if resp.status_code == 403:
                self._logged_in = False
                if not self._login():
                    return None
                resp = self._session.request(method, url, timeout=self._timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            log.debug("qbittorrent %s %s failed: %s", method, path, exc)
            self._logged_in = False
            return None

    # --- interface ---
    def get_listen_port(self) -> int | None:
        resp = self._api("GET", "/api/v2/app/preferences")
        if resp is None:
            return None
        try:
            port = resp.json().get("listen_port")
        except ValueError:
            return None
        return int(port) if isinstance(port, int) else None

    def set_listen_port(self, port: int) -> bool:
        # Also disable random-port selection so the pinned port sticks.
        body = json.dumps({"listen_port": int(port), "random_port": False})
        resp = self._api("POST", "/api/v2/app/setPreferences", data={"json": body})
        return resp is not None

    def connection_ok(self) -> bool | None:
        resp = self._api("GET", "/api/v2/transfer/info")
        if resp is None:
            return None
        try:
            status = resp.json().get("connection_status")
        except ValueError:
            return None
        if status == "disconnected":
            return False
        # "connected" and "firewalled" both mean the network layer is up
        # (firewalled = reachable but no inbound port, not a tunnel failure).
        return True
