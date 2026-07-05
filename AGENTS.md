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
| `gluetun.py`    | gluetun client: `forwarded_port` (API or `GLUETUN_PORT_FILE`), `public_ip` |
| `connectivity.py`| `OutboundProbe` — egress test via gluetun HTTP proxy, public-IP fallback |
| `dockerctl.py`  | stdlib unix-socket Docker client (`restart`/`stop`, compose resolution, `read_file`) |
| `debounce.py`   | `FailureTracker` — the anti-flap state machine               |
| `watchdog.py`   | orchestration loop + health assessment + recovery            |
| `clients/`      | `TorrentClient` interface + per-client adapters + factory    |

## Health-assessment logic (keep this exact ordering)

Health = **gluetun has working outbound connectivity**. The same
`OutboundProbe.check()` drives both the watch loop and the Docker `HEALTHCHECK`
(`gluetun-watchguard healthcheck`, exit 0/1), so compose can gate the client with
`depends_on: { condition: service_healthy }`.

1. `client.connection_ok()` → `True` ⇒ healthy, stop (cheap fast-path).
2. Otherwise `OutboundProbe.check()`, which is **tri-state**:
   - proxy request succeeds (`GLUETUN_HTTP_PROXY`) or public IP present ⇒ `UP`.
   - control server answered with no IP (and proxy didn't prove egress) ⇒ `DOWN`
     ⇒ record a failure on `tunnel_tracker`; act only once the tracker allows.
   - proxy/control server slow / timed out / errored ⇒ `UNKNOWN` ⇒ **draw no
     conclusion**: record neither success nor failure. A slow response must never
     trigger a restart — this is why `gluetun._get` returns `UNKNOWN` on transport
     errors instead of collapsing to `None`.

## Port-reachability logic (separate from tunnel health)

`client.port_is_open()` reports whether the forwarded port is actually reachable
(`qbittorrent`: `firewalled` ⇒ closed; `transmission`: `port-test`; `rutorrent`:
unknown → `None`). A closed port is **always** logged, but only feeds
`port_tracker` and triggers recovery when `PORT_CHECK_RECOVERY` is set — a closed
port usually means the provider dropped the mapping, not a tun failure.

`_recover()` is the single choke point for restarts: on success it calls
`mark_action()` on **both** trackers, so the tunnel and port paths share one
cooldown and can never chain into a double restart.

## Target resolution

`_resolve_target()` decides which container to act on, in order: explicit
`GLUETUN_CONTAINER` → `GLUETUN_SERVICE` resolved via compose labels
(`DockerSocket.resolve_compose_service`, project auto-detected from our own
container's labels or `COMPOSE_PROJECT`) → the literal `gluetun`. A `None` result
aborts the recovery with an error rather than acting on the wrong container.
Self-detection reads our own container via `hostname`, so it only works
in-container; `COMPOSE_PROJECT` is required for host/dev runs or a custom
`hostname:`. Resolution also needs read access to `GET /containers/json` and
`/containers/{id}/json` on the socket/proxy.
Resolving by service label is intentional: it survives `compose up` recreating
gluetun with a new generated name.

## Port source

`Watchdog._wanted_port()` resolves the forwarded port: the control API by
default; with `GLUETUN_PORT_FILE` set, the local file if it's mounted, else the
same file read from the gluetun container via `DockerSocket.read_file` (the
archive endpoint — no volume needed). `parse_forwarded_port` is the shared
parser. This lets port sync run without any control-server auth.

## Contributing

Local development, testing, coding conventions, and the recipes for adding a new
torrent client or environment variable live in
[CONTRIBUTING.md](./CONTRIBUTING.md).
