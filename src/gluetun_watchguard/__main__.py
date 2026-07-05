"""Entry point: ``python -m gluetun_watchguard`` / ``gluetun-watchguard``."""

from __future__ import annotations

import logging
import sys

from . import __version__
from .config import Config
from .log import setup_logging
from .watchdog import Watchdog

log = logging.getLogger("watchguard")


def main() -> int:
    try:
        cfg = Config.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg.log_level)
    log.info("gluetun-watchguard %s starting", __version__)
    Watchdog(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
