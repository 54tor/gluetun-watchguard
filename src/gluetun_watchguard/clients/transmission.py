"""Transmission RPC adapter (with X-Transmission-Session-Id handshake)."""

from __future__ import annotations

import logging

import requests

from .base import TorrentClient

log = logging.getLogger("watchguard.client.transmission")

_RPC_PATH = "/transmission/rpc"


class TransmissionClient(TorrentClient):
    kind = "transmission"

    def __init__(self, cfg) -> None:
        self._url = cfg.client_url.rstrip("/") + _RPC_PATH
        self._timeout = cfg.http_timeout
        self._session = requests.Session()
        if cfg.client_username:
            self._session.auth = (cfg.client_username, cfg.client_password)
        self._csrf: str | None = None

    def _rpc(self, method: str, arguments: dict | None = None) -> dict | None:
        payload = {"method": method, "arguments": arguments or {}}
        for _ in range(2):  # retry once to refresh the CSRF token on a 409
            headers = {}
            if self._csrf:
                headers["X-Transmission-Session-Id"] = self._csrf
            try:
                resp = self._session.post(
                    self._url, json=payload, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                log.debug("transmission rpc %s failed: %s", method, exc)
                return None
            if resp.status_code == 409:
                self._csrf = resp.headers.get("X-Transmission-Session-Id")
                continue
            if resp.status_code != 200:
                log.debug("transmission rpc %s http %s", method, resp.status_code)
                return None
            try:
                data = resp.json()
            except ValueError:
                return None
            if data.get("result") != "success":
                log.debug("transmission rpc %s result=%s", method, data.get("result"))
                return None
            return data.get("arguments", {})
        return None

    def get_listen_port(self) -> int | None:
        args = self._rpc("session-get", {"fields": ["peer-port"]})
        if not args:
            return None
        port = args.get("peer-port")
        return int(port) if isinstance(port, int) else None

    def set_listen_port(self, port: int) -> bool:
        args = self._rpc(
            "session-set",
            {"peer-port": int(port), "peer-port-random-on-start": False},
        )
        return args is not None

    def connection_ok(self) -> bool | None:
        # Transmission exposes no direct "internet" flag; rely on gluetun.
        return None

    def port_is_open(self) -> bool | None:
        # Transmission's own port-test service reports real inbound reachability.
        args = self._rpc("port-test")
        if not args:
            return None
        value = args.get("port-is-open")
        return bool(value) if isinstance(value, bool) else None
