"""ruTorrent / rTorrent adapter via XML-RPC (httprpc plugin).

Requires the ruTorrent ``httprpc`` plugin (default endpoint
``/plugins/httprpc/action.php``), which proxies XML-RPC to rTorrent.
"""

from __future__ import annotations

import logging
import xmlrpc.client

import requests

from .base import TorrentClient

log = logging.getLogger("watchguard.client.rutorrent")


class RutorrentClient(TorrentClient):
    kind = "rutorrent"

    def __init__(self, cfg) -> None:
        self._url = cfg.client_url.rstrip("/") + cfg.rutorrent_rpc_path
        self._timeout = cfg.http_timeout
        self._session = requests.Session()
        if cfg.client_username:
            self._session.auth = (cfg.client_username, cfg.client_password)

    def _call(self, method: str, *params):
        body = xmlrpc.client.dumps(params, methodname=method)
        try:
            resp = self._session.post(
                self._url,
                data=body.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result, _ = xmlrpc.client.loads(resp.content)
        except (requests.RequestException, xmlrpc.client.Fault, ValueError) as exc:
            log.debug("rutorrent %s failed: %s", method, exc)
            return None
        return result[0] if result else None

    def get_listen_port(self) -> int | None:
        # rTorrent stores a range like "6890-6890"; take the low bound.
        value = self._call("network.port_range", "")
        if not value:
            return None
        low = str(value).split("-", 1)[0]
        try:
            return int(low)
        except ValueError:
            return None

    def set_listen_port(self, port: int) -> bool:
        # Pin the port: disable randomisation, then set a single-value range.
        self._call("network.port_random.set", "", "0")
        result = self._call("network.port_range.set", "", f"{port}-{port}")
        return result is not None

    def connection_ok(self) -> bool | None:
        return None
