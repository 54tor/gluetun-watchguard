# gluetun-watchguard

[![ci](https://github.com/sat0r/gluetun-watchguard/actions/workflows/ci.yml/badge.svg)](https://github.com/sat0r/gluetun-watchguard/actions/workflows/ci.yml)
[![Docker Hub](https://img.shields.io/docker/pulls/sat0r/gluetun-watchguard)](https://hub.docker.com/r/sat0r/gluetun-watchguard)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](./LICENCE)

A small, dependency-light sidecar that keeps a torrent stack behind
[gluetun](https://github.com/qdm12/gluetun) both **correctly configured** and
**healthy** — without ever falling into restart loops.

It does three things on a fixed interval:

- **Port sync** — mirrors gluetun's VPN-forwarded NAT port into your torrent
  client so incoming connections keep working after every reconnection.
- **Tunnel health check** — confirms the client has connectivity and, when in
  doubt, verifies gluetun still has a public IP (i.e. the `tun` interface is up).
- **Guarded recovery** — restarts the gluetun container via the Docker socket
  **only** when the tunnel is confirmed down for a sustained period.

Supported clients: **qBittorrent**, **Transmission**, **ruTorrent** (rTorrent).

Image: **`sat0r/gluetun-watchguard`**.

## Why

gluetun rotates its forwarded port on every reconnection, and a dropped `tun`
interface silently kills connectivity for everything sharing its network
namespace. Manually re-entering the port or babysitting the tunnel is tedious.
`gluetun-watchguard` automates both — while being deliberately conservative
about restarts, so a slow disk or a container that is simply slow to come back
up never causes a restart storm.

## How it works

```
 gluetun control API ─────────┐
 (/v1/openvpn/portforwarded)  │    every CHECK_INTERVAL seconds:
 (/v1/publicip/ip)            │
                              ├─▶ 1. port sync   : forwarded port ≠ client port → update client
 torrent client API ──────────┤    2. health check : client OK? else gluetun has a public IP?
                              │                     └─ no  → count a failure
 Docker socket ───────────────┘    3. recovery     : N sustained failures, past grace & cooldown
                                                     → restart the gluetun container
```

The recovery step is gated by an anti-flap state machine. A restart happens only
when **all** of these hold:

- `FAILURE_THRESHOLD` consecutive failed health checks, **and**
- the process is past its `STARTUP_GRACE` window (fresh boot / just restarted),
  **and**
- at least `RESTART_COOLDOWN` seconds have passed since the last action.

The **authoritative** signal for "the tunnel is down" is gluetun losing its
public IP — not what the torrent client reports. A client that says
"firewalled"/"disconnected" while gluetun still has a public IP is treated as a
client-side issue and never triggers a gluetun restart. A **slow or unreachable**
control server is treated as *unknown* — logged but never acted on; only a
definitive "no public IP" answer counts as tunnel-down, so latency alone can
never cause a restart.

### Forwarded-port reachability

Beyond syncing the port, `gluetun-watchguard` can check whether the forwarded
port is actually **open from the outside** (qBittorrent's `firewalled` status;
Transmission's built-in `port-test`; ruTorrent has no reliable native signal).
By default a closed port is only logged as a warning. Set
`PORT_CHECK_RECOVERY=true` to let a *sustained* closed port trigger a gluetun
restart (to re-request a port from the VPN provider) — gated by the same
anti-flap logic, and sharing the tunnel check's cooldown so the two recovery
paths never chain into a double restart.

### Readiness & container health

The container ships a Docker `HEALTHCHECK`: it is **healthy only when gluetun has
working outbound connectivity**. The probe first makes a request *through
gluetun's HTTP proxy* (`GLUETUN_HTTP_PROXY`, i.e. `HTTPPROXY=on` on gluetun) —
direct proof of egress — and falls back to the control server's public IP when no
proxy is configured. The same probe drives the watch loop's health decision.

Because the health status reflects real egress, you can gate the rest of the
stack on it in compose, so the torrent client only starts once the tunnel is
actually up:

```yaml
  qbittorrent:
    depends_on:
      gluetun:
        condition: service_started
      watchguard:
        condition: service_healthy
```

You can also run the probe once by hand: `gluetun-watchguard healthcheck`
(exit `0` = healthy, `1` = not).

## Quick start (Docker Compose)

A full example lives in [`docker-compose.example.yml`](./docker-compose.example.yml).
The essentials:

```yaml
services:
  watchguard:
    image: sat0r/gluetun-watchguard:latest
    container_name: gluetun-watchguard
    environment:
      - TORRENT_CLIENT=qbittorrent
      - GLUETUN_CONTROL_URL=http://gluetun:8000
      - CLIENT_URL=http://gluetun:8080
      - CLIENT_USERNAME=admin
      - CLIENT_PASSWORD=adminadmin
      - GLUETUN_CONTAINER=gluetun
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    restart: unless-stopped
```

When the torrent client uses `network_mode: "service:gluetun"`, its WebUI is
reachable at gluetun's hostname (`http://gluetun:8080`), which is why
`CLIENT_URL` points there.

## Configuration

All configuration is via environment variables.

| Variable                | Default                       | Description                                                |
| ----------------------- | ----------------------------- | ---------------------------------------------------------- |
| `LOG_LEVEL`             | `INFO`                        | Logging level.                                             |
| `CHECK_INTERVAL`        | `30`                          | Seconds between watch ticks.                               |
| `HEALTHCHECK_URL`       | `http://cp.cloudflare.com/generate_204` | URL fetched to prove gluetun egress works.       |
| `ENABLE_PORT_SYNC`      | `true`                        | Enable forwarded-port synchronisation.                     |
| `ENABLE_HEALTHCHECK`    | `true`                        | Enable tunnel health checking.                             |
| `ENABLE_PORT_CHECK`     | `true`                        | Check whether the forwarded port is actually reachable.    |
| `ENABLE_DOCKER_ACTION`  | `true`                        | Allow the recovery action to touch the Docker socket.      |
| `GLUETUN_CONTROL_URL`   | `http://gluetun:8000`         | gluetun control-server base URL.                           |
| `GLUETUN_HTTP_PROXY`    | _(empty)_                     | gluetun HTTP proxy (`HTTPPROXY=on`) for the egress probe.  |
| `GLUETUN_PORT_FILE`     | _(empty)_                     | Read the forwarded port from this file instead of the API. |
| `GLUETUN_API_KEY`       | _(empty)_                     | `X-API-Key` header, if the control server requires auth.   |
| `GLUETUN_AUTH_USERNAME` | _(empty)_                     | HTTP basic-auth user for the control server (alternative). |
| `GLUETUN_AUTH_PASSWORD` | _(empty)_                     | HTTP basic-auth password.                                  |
| `TORRENT_CLIENT`        | `qbittorrent`                 | `qbittorrent` \| `transmission` \| `rutorrent`.            |
| `CLIENT_URL`            | `http://gluetun:8080`         | Torrent client base URL.                                   |
| `CLIENT_USERNAME`       | _(empty)_                     | Torrent client username.                                   |
| `CLIENT_PASSWORD`       | _(empty)_                     | Torrent client password.                                   |
| `RUTORRENT_RPC_PATH`    | `/plugins/httprpc/action.php` | ruTorrent httprpc endpoint path (ruTorrent only).          |
| `DOCKER_SOCKET`         | `/var/run/docker.sock`        | Path to the Docker socket (or a socket-proxy).             |
| `GLUETUN_CONTAINER`     | _(empty)_                     | Explicit container name/id to act on (highest precedence). |
| `GLUETUN_SERVICE`       | _(empty)_                     | Compose service to resolve to a container (same project).  |
| `COMPOSE_PROJECT`       | _(auto)_                      | Compose project for resolution; auto-detected if empty.    |
| `DOCKER_ACTION`         | `restart`                     | `restart` \| `stop` \| `none`.                             |
| `PORT_CHECK_RECOVERY`   | `false`                       | Let a sustained closed forwarded port trigger recovery.    |
| `FAILURE_THRESHOLD`     | `3`                           | Consecutive failed checks before acting.                   |
| `RESTART_COOLDOWN`      | `300`                         | Minimum seconds between Docker actions.                    |
| `STARTUP_GRACE`         | `60`                          | Seconds to ignore failures after start / after an action.  |
| `HTTP_TIMEOUT`          | `10`                          | Timeout (seconds) for API calls.                           |

### gluetun control server

`gluetun-watchguard` reads the forwarded port and public IP from gluetun's
control server (port `8000`). Ensure it is enabled and reachable on your
Docker network. Recent gluetun versions may require authentication for the
control API — if so, set `GLUETUN_API_KEY` (or the basic-auth pair) to match
your gluetun control-server configuration.

### Forwarded port without the control server

To skip control-server auth for port sync, point `GLUETUN_PORT_FILE` at gluetun's
forwarded-port status file (`VPN_PORT_FORWARDING_STATUS_FILE`, default
`/tmp/gluetun/forwarded_port`). watchguard then obtains the port from that file
instead of the control API — and **no volume is required**: when the path isn't
present locally, it reads the file straight from the gluetun container over the
Docker socket it already holds (the same one used for recovery). Combined with
`GLUETUN_HTTP_PROXY` for the health probe, the whole stack runs without any
control-server authentication.

```yaml
  watchguard:
    environment:
      - GLUETUN_PORT_FILE=/tmp/gluetun/forwarded_port   # read via the Docker socket
      - GLUETUN_HTTP_PROXY=http://gluetun:8888
```

Prefer a volume? Mount `/tmp/gluetun` on both services at the same path — when the
file exists locally, watchguard reads it directly and skips the socket. Reading
through the socket needs the Docker API to allow `GET /containers/{id}/archive`
(mind your socket-proxy).

### Targeting the gluetun container

The recovery action needs to identify the gluetun container. Note this is
separate from DNS: the compose service name resolves over the network (so
`CLIENT_URL=http://gluetun:8080` works), but the Docker API needs a container
name/id. Two ways to point at it:

- `GLUETUN_CONTAINER` — an explicit name/id (takes precedence). Best when you set
  `container_name:` in compose.
- `GLUETUN_SERVICE` — a compose **service** name. When `watchguard` runs in the
  same compose project, it auto-detects the project from its own container and
  resolves the service to a container via compose labels. This survives
  `compose up` recreating gluetun with a new generated name (e.g.
  `myproject-gluetun-1`). Auto-detection reads *this* container's own labels, so
  it only works when `watchguard` itself runs as a container in the project — set
  `COMPOSE_PROJECT` explicitly when running outside a container (e.g. local
  `make run`) or when the container uses a custom `hostname:`. Service resolution
  also needs the Docker API to allow `GET /containers/json` and
  `/containers/{id}/json` (mind your socket-proxy).

## Security notes

The recovery action requires access to the Docker socket, which is a
**privileged** resource: anything that can talk to it can control the host's
Docker daemon. To reduce exposure:

- Put a **docker-socket-proxy** in front of the socket, allow only the container
  `restart`/`stop` calls (plus `GET /containers/json` and `/containers/{id}/json`
  for `GLUETUN_SERVICE`, and `GET /containers/{id}/archive` for `GLUETUN_PORT_FILE`
  read via the socket), and point `DOCKER_SOCKET` at the proxy.
- Or set `DOCKER_ACTION=none` / `ENABLE_DOCKER_ACTION=false` to run in
  observe-and-port-sync mode only, and handle tunnel recovery yourself.

## Troubleshooting

### `Failed to resolve 'gluetun'` / name-resolution errors

```
watchguard.gluetun: gluetun GET /v1/... failed: ... Failed to resolve 'gluetun'
```

`watchguard` can't resolve the `gluetun` hostname, so it reaches neither the
control server, the HTTP proxy, nor the client. (It correctly reports health as
*unknown* and does **not** restart anything in this state.)

Docker's built-in DNS only resolves a service name when the containers share a
**user-defined network**. Run `watchguard` as its own container **on the same
network as gluetun** — not with `network_mode: "service:gluetun"`, otherwise a
gluetun restart would take the watchdog's own network down with it.

- **Same compose file?** Services share the default network automatically — just
  make sure the gluetun service is really named `gluetun` and that `watchguard`
  has no `network_mode`.
- **Different compose project / `docker run`?** Attach `watchguard` to gluetun's
  network explicitly:

  ```yaml
  services:
    watchguard:
      networks: [gluetun_net]
  networks:
    gluetun_net:
      external: true
      name: <gluetun's network, from `docker network ls`>
  ```

- Verify from inside the container (empty output ⇒ wrong network):

  ```bash
  docker exec gluetun-watchguard getent hosts gluetun
  ```

- Or bypass names entirely and point the URLs at the right host:
  `GLUETUN_CONTROL_URL`, `CLIENT_URL`, `GLUETUN_HTTP_PROXY`.

### Control server returns 401 / 403

gluetun v3.40+ requires authentication on its control server. Grant watchguard
the two routes it reads, e.g. in gluetun's `/gluetun/auth/config.toml`:

```toml
[[roles]]
name = "watchguard"
routes = ["GET /v1/openvpn/portforwarded", "GET /v1/publicip/ip"]
auth = "apikey"
apikey = "change-me"
```

then set `GLUETUN_API_KEY=change-me` on watchguard (or use `auth = "none"` for
those routes to skip the key). Basic auth is also supported via
`GLUETUN_AUTH_USERNAME` / `GLUETUN_AUTH_PASSWORD`.

### `target container unresolved` when using `GLUETUN_SERVICE`

Compose-service resolution needs the Docker API to allow `GET /containers/json`
and `/containers/{id}/json`, and project auto-detection only works in-container —
set `COMPOSE_PROJECT` for host/dev runs. See *Targeting the gluetun container*.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for local development, testing, and
coding conventions, and [AGENTS.md](./AGENTS.md) for architecture and design
constraints.

## License

[GPL-3.0-or-later](./LICENCE).
