# AGENTS.md — GLUETUN-WATCHGUARD

Working notes for anyone (human or agent) touching this repository. Read this
before making changes.

## Synopsis

GLUETUN-WATCHGUARD is a lightweight sidecar daemon that keeps a torrent stack
sitting behind [gluetun](https://github.com/qdm12/gluetun) healthy and correctly
configured. On a fixed interval it does three things:

1. **Port sync** — reads the VPN-forwarded NAT port from the gluetun control
   server and pushes it into the torrent client (qBittorrent / Transmission /
   ruTorrent) whenever the two drift apart.
2. **Tunnel health check** — verifies connectivity cheaply via the torrent
   client, then escalates to gluetun's public-IP endpoint to confirm the `tun`
   interface is actually up and routing.
3. **Guarded recovery** — when (and only when) the tunnel is confirmed down for
   a sustained period, restarts the gluetun container through the Docker socket.

It ships as the public image **`sat0r/gluetun-watchguard`**.

## Non-negotiable constraints

- **No restart storms.** A slow disk, a container that is merely slow to boot,
  or a single flaky probe must never trigger a restart. All recovery is gated by
  `FailureTracker` (`debounce.py`): N consecutive failures **AND** past the
  startup grace window **AND** past the cooldown since the last action. When in
  doubt, do *not* act.
- **gluetun's public IP is the authoritative tunnel signal.** The torrent
  client's own status is only a cheap first hint. We restart gluetun solely when
  its public IP is gone (tun down) — never merely because a client says
  "disconnected" or "firewalled".
- **Anonymous.** No personal names, emails, hostnames, or absolute host paths in
  code, docs, comments, or examples. Author is `Sat0r`; canonical references are
  `github.com/sat0r/gluetun-watchguard` and Docker Hub `sat0r/gluetun-watchguard`.
- **Minimal dependencies.** Standard library everywhere, plus `requests` for
  HTTP. The Docker socket is spoken to with a tiny stdlib `http.client` shim
  (`dockerctl.py`) — do not pull in the heavy `docker` SDK.
- **Multi-client by design.** Every client lives behind the `TorrentClient`
  interface (`clients/base.py`). Add a client by implementing that interface and
  registering it in `build_client` + `SUPPORTED_CLIENTS`; touch nothing else.

## Architecture

```
gluetun control API ──┐
                      ├─▶ Watchdog.tick()  (loop.py: run → tick every CHECK_INTERVAL)
torrent client API ───┤        ├─ sync_port()     : port drift → client.set_listen_port()
                      │        └─ check_health()  : assess_health() → FailureTracker → act()
Docker socket ────────┘                                                         └─▶ restart gluetun
```

Modules (`src/gluetun_watchguard/`):

| File            | Responsibility                                               |
|-----------------|--------------------------------------------------------------|
| `config.py`     | `Config` dataclass; parses & validates env vars              |
| `log.py`        | stdout logging setup                                          |
| `gluetun.py`    | gluetun control-server client (`forwarded_port`, `public_ip`)|
| `dockerctl.py`  | stdlib unix-socket Docker client (`restart`, `stop`)         |
| `debounce.py`   | `FailureTracker` — the anti-flap state machine               |
| `watchdog.py`   | orchestration loop + health assessment + recovery            |
| `clients/`      | `TorrentClient` interface + per-client adapters + factory    |

## Health-assessment logic (keep this exact ordering)

1. `client.connection_ok()` → `True` ⇒ healthy, stop (no gluetun call needed).
2. Otherwise query `gluetun.public_ip()`:
   - IP present ⇒ tunnel up. If the client said "down", it's a client-side
     issue — log it, do **not** restart gluetun.
   - IP absent ⇒ tunnel down ⇒ record a failure; act only once the
     `FailureTracker` allows.

## Contributing

Local development, testing, coding conventions, and the recipes for adding a new
torrent client or environment variable live in
[CONTRIBUTING.md](./CONTRIBUTING.md).
