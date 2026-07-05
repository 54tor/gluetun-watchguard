# Contributing to gluetun-watchguard

Thanks for helping out! This guide covers local development, testing, and the
conventions this project follows. For the system's architecture and its
non-negotiable design constraints, see [AGENTS.md](./AGENTS.md).

## Requirements

- Python 3.11+
- Docker (only needed to build or run the image)

## Development setup

```bash
make install     # editable install with dev extras: pip install -e ".[dev]"
make test        # run the offline test suite (pytest)
make lint        # ruff check .
make run         # python -m gluetun_watchguard  (reads config from env)
make build       # docker build -t sat0r/gluetun-watchguard:dev .
```

Configuration is entirely environment-driven. Copy [`.env.example`](./.env.example)
to `.env` and adjust it for local runs; the full variable reference lives in the
[README](./README.md).

## Project layout

| Path                              | Responsibility                                        |
|-----------------------------------|-------------------------------------------------------|
| `src/gluetun_watchguard/config.py`| `Config` dataclass; parses & validates env vars       |
| `src/gluetun_watchguard/log.py`   | stdout logging setup                                  |
| `src/gluetun_watchguard/gluetun.py`| gluetun control-server client                        |
| `src/gluetun_watchguard/dockerctl.py`| stdlib unix-socket Docker client                   |
| `src/gluetun_watchguard/debounce.py`| `FailureTracker` — anti-flap state machine          |
| `src/gluetun_watchguard/watchdog.py`| orchestration loop + health assessment + recovery   |
| `src/gluetun_watchguard/clients/` | `TorrentClient` interface, adapters and factory       |
| `tests/`                          | offline unit tests                                    |

## Testing

- The suite is **fully offline**: the gluetun, torrent-client and Docker
  collaborators are faked, and `FailureTracker` takes an injectable `clock` so
  time-based behaviour is deterministic.
- No test may hit the network or a real Docker socket.
- Add or update tests alongside any behaviour change, and keep them offline.

## Coding conventions

- Python 3.11+, `from __future__ import annotations`, type hints on public APIs.
- Keep `ruff check .` clean (rules `E, F, I, UP, B`; 100-column lines).
- Configuration comes from environment variables only (12-factor); no runtime
  config files.
- Logging levels: `INFO` for state changes (port synced, recovery), `WARNING`
  for failures, `DEBUG` for per-probe detail. Never log secrets.
- Keep dependencies minimal: standard library plus `requests`. Talk to the
  Docker socket through the stdlib shim in `dockerctl.py` — do not add the
  heavyweight `docker` SDK.

## Adding a torrent client

1. Implement the `TorrentClient` interface in
   `src/gluetun_watchguard/clients/<name>.py` (`get_listen_port`,
   `set_listen_port`, and optionally `connection_ok`).
2. Register it in `build_client` (`clients/base.py`) and add it to
   `SUPPORTED_CLIENTS` (`config.py`).
3. Add tests and update the supported-clients list in the README.

## Adding an environment variable

Every new variable must be added in three places, kept in sync:

1. `Config` (`config.py`) — the field plus its parsing in `from_env`.
2. `.env.example` — a documented default.
3. The configuration table in the README.

## Commit conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): summary`, e.g. `feat(clients): ...`, `fix(watchdog): ...`,
`docs: ...`, `test: ...`, `build: ...`, `ci: ...`, `chore: ...`. Keep each
commit scoped to a single concern.

## Design constraints

Before touching health or recovery logic, read the **non-negotiable
constraints** in [AGENTS.md](./AGENTS.md): recovery must never cause restart
storms, and gluetun's public IP is the authoritative tunnel signal.
