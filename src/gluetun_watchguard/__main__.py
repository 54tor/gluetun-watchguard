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
    argv = sys.argv[1:]
    try:
        cfg = Config.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg.log_level)
    if argv and argv[0] == "healthcheck":
        return _run_healthcheck(cfg)
    log.info("gluetun-watchguard %s starting", __version__)
    Watchdog(cfg).run()
    return 0


def _run_healthcheck(cfg: Config) -> int:
    """One-shot outbound-connectivity probe for Docker HEALTHCHECK. 0=healthy."""
    from .connectivity import HEALTH_UP, OutboundProbe
    from .gluetun import build_gluetun

    state = OutboundProbe(cfg, build_gluetun(cfg)).check()
    log.info("healthcheck: gluetun outbound %s", state)
    return 0 if state == HEALTH_UP else 1


if __name__ == "__main__":
    sys.exit(main())
