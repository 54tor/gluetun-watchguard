"""Runtime configuration, loaded entirely from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

TRUE_VALUES = {"1", "true", "yes", "on"}
SUPPORTED_CLIENTS = ("qbittorrent", "transmission", "rutorrent")


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # --- General ---
    log_level: str = "INFO"
    check_interval: int = 30  # seconds between watch ticks

    # --- Feature toggles ---
    enable_port_sync: bool = True
    enable_healthcheck: bool = True
    enable_port_check: bool = True
    enable_docker_action: bool = True

    # --- gluetun control server ---
    gluetun_url: str = "http://gluetun:8000"
    gluetun_api_key: str = ""
    gluetun_username: str = ""
    gluetun_password: str = ""

    # --- Torrent client ---
    client_kind: str = "qbittorrent"
    client_url: str = "http://gluetun:8080"
    client_username: str = ""
    client_password: str = ""
    rutorrent_rpc_path: str = "/plugins/httprpc/action.php"

    # --- Docker recovery action ---
    docker_socket: str = "/var/run/docker.sock"
    gluetun_container: str = ""  # explicit name/id; takes precedence over the service
    gluetun_service: str = ""  # compose service name, resolved to a container via labels
    compose_project: str = ""  # compose project for resolution; auto-detected if empty
    docker_action: str = "restart"  # restart | stop | none
    port_check_recovery: bool = False  # let a closed forwarded port trigger recovery

    # --- Anti-flap / debounce ---
    failure_threshold: int = 3  # consecutive failed checks before acting
    restart_cooldown: int = 300  # min seconds between docker actions
    startup_grace: int = 60  # seconds to ignore failures after start / action
    http_timeout: int = 10  # seconds for API calls

    @classmethod
    def from_env(cls) -> Config:
        client_kind = _env_str("TORRENT_CLIENT", "qbittorrent").lower()
        if client_kind not in SUPPORTED_CLIENTS:
            raise ValueError(
                f"Unsupported TORRENT_CLIENT={client_kind!r}; "
                f"expected one of {', '.join(SUPPORTED_CLIENTS)}"
            )
        action = _env_str("DOCKER_ACTION", "restart").lower()
        if action not in ("restart", "stop", "none"):
            raise ValueError(
                f"Unsupported DOCKER_ACTION={action!r}; expected restart, stop or none"
            )
        return cls(
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            check_interval=_env_int("CHECK_INTERVAL", 30),
            enable_port_sync=_env_bool("ENABLE_PORT_SYNC", True),
            enable_healthcheck=_env_bool("ENABLE_HEALTHCHECK", True),
            enable_port_check=_env_bool("ENABLE_PORT_CHECK", True),
            enable_docker_action=_env_bool("ENABLE_DOCKER_ACTION", True),
            gluetun_url=_env_str("GLUETUN_CONTROL_URL", "http://gluetun:8000").rstrip("/"),
            gluetun_api_key=_env_str("GLUETUN_API_KEY"),
            gluetun_username=_env_str("GLUETUN_AUTH_USERNAME"),
            gluetun_password=_env_str("GLUETUN_AUTH_PASSWORD"),
            client_kind=client_kind,
            client_url=_env_str("CLIENT_URL", "http://gluetun:8080").rstrip("/"),
            client_username=_env_str("CLIENT_USERNAME"),
            client_password=_env_str("CLIENT_PASSWORD"),
            rutorrent_rpc_path=_env_str("RUTORRENT_RPC_PATH", "/plugins/httprpc/action.php"),
            docker_socket=_env_str("DOCKER_SOCKET", "/var/run/docker.sock"),
            gluetun_container=_env_str("GLUETUN_CONTAINER"),
            gluetun_service=_env_str("GLUETUN_SERVICE"),
            compose_project=_env_str("COMPOSE_PROJECT"),
            docker_action=action,
            port_check_recovery=_env_bool("PORT_CHECK_RECOVERY", False),
            failure_threshold=_env_int("FAILURE_THRESHOLD", 3),
            restart_cooldown=_env_int("RESTART_COOLDOWN", 300),
            startup_grace=_env_int("STARTUP_GRACE", 60),
            http_timeout=_env_int("HTTP_TIMEOUT", 10),
        )
