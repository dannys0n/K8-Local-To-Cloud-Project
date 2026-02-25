#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Worker teardown =="

sudo_if_needed systemctl stop k3s-agent || true
sudo_if_needed systemctl disable k3s-agent || true


restore_iptables_mode() {
  local state_file="/etc/k3s-lab/iptables-mode.env"
  if [[ ! -f "$state_file" ]]; then
    return 0
  fi
  if ! command -v update-alternatives >/dev/null 2>&1; then
    return 0
  fi

  # shellcheck disable=SC1090
  source "$state_file"

  if [[ -n "${IPTABLES_ORIG_VALUE:-}" ]]; then
    echo "[INFO] Restoring iptables alternatives."
    sudo_if_needed update-alternatives --set iptables "$IPTABLES_ORIG_VALUE" || true
  fi
  if [[ -n "${IP6TABLES_ORIG_VALUE:-}" ]]; then
    sudo_if_needed update-alternatives --set ip6tables "$IP6TABLES_ORIG_VALUE" || true
  fi
}

restore_iptables_mode

if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
  sudo_if_needed /usr/local/bin/k3s-agent-uninstall.sh || true
fi

sudo_if_needed rm -rf /etc/k3s-lab || true

echo "Worker teardown complete."
