\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Server up =="

sudo_if_needed systemctl start k3s
sudo_if_needed systemctl start k3s-lab-token || true

# Refresh token file (in case it changed)
if [[ -f /var/lib/rancher/k3s/server/node-token ]]; then
  sudo_if_needed sh -c "cat /var/lib/rancher/k3s/server/node-token > /opt/k3s-lab/agent-token"
  sudo_if_needed chmod 0644 /opt/k3s-lab/agent-token
fi

echo "Waiting for k3s API..."
if ! wait_for_k3s 60; then
  echo "k3s not ready. Check: sudo journalctl -u k3s -n 200 --no-pager"
  exit 1
fi

kubectl get nodes -o wide
