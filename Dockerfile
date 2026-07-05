# syntax=docker/dockerfile:1
FROM python:3.12-slim

LABEL org.opencontainers.image.title="gluetun-watchguard" \
      org.opencontainers.image.description="Port-sync and tunnel watchdog for gluetun stacks" \
      org.opencontainers.image.source="https://github.com/sat0r/gluetun-watchguard" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The recovery action needs the Docker socket, which is owned by root:docker on
# the host. The container therefore runs as root by default; harden at runtime
# with a docker-socket-proxy and/or `group_add` (see README).
ENTRYPOINT ["gluetun-watchguard"]
