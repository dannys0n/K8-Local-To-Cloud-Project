#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/../../lib/common.sh"

echo "== Agent teardown =="

sudo_if_needed systemctl stop k3s-agent || true
sudo_if_needed systemctl disable k3s-agent || true

if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
  sudo_if_needed /usr/local/bin/k3s-agent-uninstall.sh || true
fi

sudo_if_needed rm -rf /etc/k3s-lab || true

echo "Agent teardown complete."
