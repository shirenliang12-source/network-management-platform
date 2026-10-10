# Cisco Network Manager — Linux container (multi-stage)
#
# Stage 1 (builder): compile the one-file Linux binary with PyInstaller.
# Stage 2 (runtime): a minimal image containing only the binary + shared libs.
#
# Build the image:
#   docker build -t cisco-netmgr:1.9.41 -f linux/Dockerfile .
#
# Run directly:
#   docker run -d --name cisco-netmgr -p 9632:9632 \
#       -v cisco-netmgr-data:/data cisco-netmgr:1.9.41
#
# Extract the bare binary (if you want to deploy it on a bare-metal host):
#   docker create --name extract cisco-netmgr:1.9.41
#   docker cp extract:/usr/local/bin/CiscoNetworkManager ./CiscoNetworkManager
#   docker rm extract
#
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libssl-dev libffi-dev git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt pyinstaller

COPY . .
RUN pyinstaller build_onefile_linux.spec --noconfirm --distpath dist --workpath build/pyi \
    && chmod +x dist/CiscoNetworkManager

# ---- runtime image ----
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libssl3 libffi8 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r netmgr && useradd -r -g netmgr -s /usr/sbin/nologin netmgr

COPY --from=builder /build/dist/CiscoNetworkManager /usr/local/bin/CiscoNetworkManager

VOLUME ["/data"]
RUN mkdir -p /data/backups /data/exports && chown -R netmgr:netmgr /data
USER netmgr
WORKDIR /data

ENV NETMGR_HOST=0.0.0.0 NETMGR_PORT=9632 CISCO_NM_DATA_DIR=/data
EXPOSE 9632

ENTRYPOINT ["CiscoNetworkManager", "--host", "0.0.0.0", "--port", "9632", "--data-dir", "/data"]
