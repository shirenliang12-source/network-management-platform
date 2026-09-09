#!/usr/bin/env bash
#
# build_linux.sh — Build the Cisco Network Manager one-file Linux binary.
#
# Run on a Linux host (x86_64). Requires: python3.10+, pip, build tools.
# Produces: dist/CiscoNetworkManager  (self-contained, no .exe suffix)
#
# Usage:
#   ./build_linux.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PYBIN="$(command -v python3 || command -v python)"
echo "==> Using ${PYBIN} ($(${PYBIN} --version 2>&1))"

# Create / reuse a venv
VENV="${ROOT}/.venv_linux"
if [[ ! -x "${VENV}/bin/activate" ]]; then
  "${PYBIN}" -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

# Build one-file binary with the Linux spec
pyinstaller build_onefile_linux.spec --noconfirm --distpath dist --workpath build/linux

echo
echo "==> Built: ${ROOT}/dist/CiscoNetworkManager"
echo "    Deploy with: sudo linux/install_linux.sh dist/CiscoNetworkManager"
