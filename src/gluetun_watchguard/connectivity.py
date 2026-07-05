"""Outbound connectivity probe: is gluetun actually reaching the internet?

Shared by the watch loop (recovery decision) and the ``healthcheck`` CLI mode
(Docker HEALTHCHECK / compose readiness gating). A successful request *through
gluetun's HTTP proxy* is direct proof of working egress; when no proxy is
configured (or it fails) we fall back to the control server's public IP.
"""

from __future__ import annotations

import logging

import requests

from .gluetun import UNKNOWN, GluetunControl

log = logging.getLogger("watchguard.connectivity")

HEALTH_UP = "up"
HEALTH_DOWN = "down"
HEALTH_UNKNOWN = "unknown"


class OutboundProbe:
    def __init__(self, cfg, gluetun: GluetunControl) -> None:
        self._proxy = cfg.gluetun_http_proxy
        self._url = cfg.healthcheck_url
        self._timeout = cfg.http_timeout
        self._gluetun = gluetun
        self._session = requests.Session()

    def check(self) -> str:
        """Return HEALTH_UP / HEALTH_DOWN / HEALTH_UNKNOWN."""
        if self._proxy:
            # Direct proof: reach the internet through gluetun's HTTP proxy.
            if self._probe_proxy() == HEALTH_UP:
                return HEALTH_UP
            # Proxy unreachable/slow (or not enabled): fall back below.
        return self._probe_public_ip()

    def _probe_proxy(self) -> str:
        proxies = {"http": self._proxy, "https": self._proxy}
        try:
            resp = self._session.get(
                self._url, proxies=proxies, timeout=self._timeout, allow_redirects=False
            )
        except requests.RequestException as exc:
            log.debug("outbound proxy probe failed: %s", exc)
            return HEALTH_UNKNOWN
        # Any HTTP response means we reached the internet through gluetun.
        log.debug("outbound proxy probe: HTTP %s", resp.status_code)
        return HEALTH_UP

    def _probe_public_ip(self) -> str:
        ip = self._gluetun.public_ip()
        if ip is UNKNOWN:
            return HEALTH_UNKNOWN
        if ip:
            return HEALTH_UP
        return HEALTH_DOWN
