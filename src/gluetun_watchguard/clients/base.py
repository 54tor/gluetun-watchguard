"""Abstract torrent-client adapter + factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger("watchguard.client")


class TorrentClient(ABC):
    kind = "base"

    @abstractmethod
    def get_listen_port(self) -> int | None:
        """Current incoming/listen port, or None if it can't be read."""

    @abstractmethod
    def set_listen_port(self, port: int) -> bool:
        """Set the incoming/listen port. Returns True on success."""

    def connection_ok(self) -> bool | None:
        """Best-effort view of whether the client sees working connectivity.

        Returns ``True``/``False`` when the client exposes such a signal, or
        ``None`` when it is unknown/unsupported (callers must then fall back to
        the authoritative gluetun public-IP check).
        """
        return None

    def port_is_open(self) -> bool | None:
        """Whether the incoming/forwarded port is actually reachable from outside.

        Returns ``True``/``False`` when the client can tell, or ``None`` when it
        is unknown/unsupported (callers must not draw any conclusion then).
        """
        return None


def build_client(cfg) -> TorrentClient:
    # Imported lazily to keep optional per-client deps out of the hot path.
    from .qbittorrent import QbittorrentClient
    from .rutorrent import RutorrentClient
    from .transmission import TransmissionClient

    mapping = {
        "qbittorrent": QbittorrentClient,
        "transmission": TransmissionClient,
        "rutorrent": RutorrentClient,
    }
    try:
        klass = mapping[cfg.client_kind]
    except KeyError:  # pragma: no cover - guarded by Config.from_env
        raise ValueError(f"Unsupported client kind: {cfg.client_kind}") from None
    return klass(cfg)
