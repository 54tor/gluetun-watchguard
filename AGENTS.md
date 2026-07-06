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
| `dockerctl.py`  | stdlib socket Docker client (`restart`/`stop`/`start`, resolution, `read_file`, `container_state`) |
| `debounce.py`   | `FailureTracker` — the anti-flap state machine               |
| `watchdog.py`   | orchestration loop + health assessment + recovery            |
| `clients/`      | `TorrentClient` interface + per-client adapters + factory    |

## Health-assessment logic (keep this exact ordering)

Health = **gluetun has working outbound connectivity**. The same
`OutboundProbe.check()` drives both the watch loop and the Docker `HEALTHCHECK`
(`gluetun-watchguard healthcheck`, exit 0/1), so compose can gate the client with
`depends_on: { condition: service_healthy }`.

0. **Container health first (authoritative).** `_gluetun_is_dead()`
   (`ENABLE_CONTAINER_HEALTH`) inspects `container_state`: `Running is False` or
   `Health=="unhealthy"` ⇒ `DOWN` immediately — gluetun's own healthcheck failing
   means gluetun says it is not routing, which outranks the client's self-reported
   status. This runs every tick, *before* the fast-path, so a `unhealthy` gluetun
   is acted on even while the client still claims "connected". A container merely
   running with no health verdict stays out of the way (latency ≠ death).
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

**Client-readiness gate.** `check_port()` is skipped until the client has been
*seen up* (`connection_ok() is True`), latched in `_client_seen_up`. This is a
state latch, **not** a timer: a client still booting (slow disk) briefly reports
`firewalled`, which must not be mistaken for a closed port. The latch is reset to
`False` whenever watchguard stops/restarts the client in `_recover()`, so after a
watchguard-initiated cycle the port is only monitored again once the client has
come back up — never during its restart window. Tunnel/container health is
independent of this gate and is always monitored.

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

## Recovery

`_recover(reason)` is the single choke point for Docker actions:

- Targets: `_resolve_target()` (gluetun) and `_resolve_client()` (torrent client)
  each honour an explicit `*_CONTAINER`, then a `*_SERVICE` compose lookup.
- `DOCKER_ACTION=none`/disabled → log only; unresolved gluetun → abort.
- **No client** configured → plain gluetun restart.
- **Client** configured → orchestrated cycle: stop client → restart gluetun → set
  `_recovery_until`; across ticks `_advance_recovery()` waits for
  `assess_health()==UP` (then starts the client) or the `RECOVERY_HEALTHY_TIMEOUT`
  deadline (starts it anyway). While recovering, `tick()` only advances recovery
  and the loop polls every `_RECOVERY_POLL`s.
- `DOCKER_ACTION=stop` → kill-switch: stop client then gluetun, no restart/wait.

On success `_mark_recovered()` marks **both** trackers (shared cooldown + re-grace)
so tunnel and port paths never chain a double restart.

**Escalations:** `_gluetun_is_dead()` (`ENABLE_CONTAINER_HEALTH`) inspects
`container_state` first every tick — `Running is False` or `Health=="unhealthy"`
is an authoritative `DOWN` that overrides the client fast-path (see the
Health-assessment ordering above). Still gated by the tracker, so gluetun keeps
its self-heal window before a container restart. `HEALTH_REQUIRE_EGRESS`
additionally skips the fast-path so egress is probed even when the container
reports healthy.

## Contributing

Local development, testing, coding conventions, and the recipes for adding a new
torrent client or environment variable live in
[CONTRIBUTING.md](./CONTRIBUTING.md).
