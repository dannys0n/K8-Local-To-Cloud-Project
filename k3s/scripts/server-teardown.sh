\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Server teardown =="

sudo_if_needed systemctl stop k3s-lab-token || true
sudo_if_needed systemctl disable k3s-lab-token || true
sudo_if_needed rm -f /etc/systemd/system/k3s-lab-token.service || true
sudo_if_needed systemctl daemon-reload || true

# Uninstall k3s if installed
if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
  sudo_if_needed /usr/local/bin/k3s-uninstall.sh || true
fi

sudo_if_needed rm -rf /opt/k3s-lab || true
rm -f "$HOME/.kube/config" "$HOME/.kube/config.bak" 2>/dev/null || true

echo "Server teardown complete."
