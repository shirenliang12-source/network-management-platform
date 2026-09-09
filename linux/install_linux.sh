#!/usr/bin/env bash
#
# install_linux.sh — Deploy Cisco Network Manager as a Linux systemd service.
#
# Usage:
#   sudo ./install_linux.sh [PATH_TO_BINARY]
#
#   PATH_TO_BINARY   Path to the built CiscoNetworkManager binary
#                     (default: ./CiscoNetworkManager in the current dir)
#
# What it does:
#   1. Copies the binary to /opt/cisco-netmgr/
#   2. Creates an unprivileged system user "netmgr"
#   3. Creates the data dir and fixes ownership
#   4. Installs the systemd unit and enables/starts the service
#   5. Opens firewall port 9632 (ufw / firewalld)
#
set -euo pipefail

APP_USER="netmgr"
INSTALL_DIR="/opt/cisco-netmgr"
DATA_DIR="${INSTALL_DIR}/data"
PORT="9632"
SERVICE_NAME="cisco-netmgr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Locate the binary
BINARY="${1:-${SCRIPT_DIR}/CiscoNetworkManager}"
if [[ ! -x "${BINARY}" ]]; then
  echo "ERROR: binary not found or not executable: ${BINARY}" >&2
  echo "Usage: sudo $0 [PATH_TO_BINARY]" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: this script must be run as root (sudo)." >&2
  exit 1
fi

echo "==> Installing Cisco Network Manager (Linux)"
echo "    binary : ${BINARY}"
echo "    target : ${INSTALL_DIR}"

# 1. Install directory + binary
mkdir -p "${INSTALL_DIR}"
install -m 0755 "${BINARY}" "${INSTALL_DIR}/CiscoNetworkManager"

# 2. System user
if ! id "${APP_USER}" &>/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "${APP_USER}"
  echo "    created user ${APP_USER}"
fi

# 3. Data directory
mkdir -p "${DATA_DIR}/backups" "${DATA_DIR}/exports"
chown -R "${APP_USER}:${APP_USER}" "${INSTALL_DIR}"
chmod 0750 "${DATA_DIR}"

# 4. systemd unit
install -m 0644 "${SCRIPT_DIR}/cisco-netmgr.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

# 5. Firewall
if command -v ufw &>/dev/null; then
  ufw allow "${PORT}/tcp" comment "Cisco Network Manager" || true
elif command -v firewall-cmd &>/dev/null; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" || true
  firewall-cmd --reload || true
fi

# 6. Start
systemctl restart "${SERVICE_NAME}"

echo
echo "==> Done. Service status:"
systemctl --no-pager status "${SERVICE_NAME}" --lines=5 || true
echo
echo "    Web UI:  http://<this-host>:${PORT}/"
echo "    Logs:    journalctl -u ${SERVICE_NAME} -f"
echo
