#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Server setup (Ubuntu server) =="

apt_install curl ca-certificates

# Install k3s server (also acts as a worker)
if [[ ! -x /usr/local/bin/k3s ]]; then
  echo "Installing k3s server via https://get.k3s.io ..."
  if ! curl -sfL https://get.k3s.io | sudo_if_needed sh -s - server \
    --write-kubeconfig-mode 644; then
    echo "k3s install/start failed. Diagnostics:"
    sudo_if_needed systemctl status k3s --no-pager -l || true
    sudo_if_needed journalctl -u k3s -n 200 --no-pager || true
    exit 1
  fi
else
  echo "k3s already installed. Ensuring service is enabled..."
fi

# Ensure service is up
if ! sudo_if_needed systemctl enable --now k3s; then
  echo "k3s service failed to start. Diagnostics:"
  sudo_if_needed systemctl status k3s --no-pager -l || true
  sudo_if_needed journalctl -u k3s -n 200 --no-pager || true
  exit 1
fi

echo "Waiting for k3s API to be ready..."
if ! wait_for_k3s 90; then
  echo "k3s did not become ready in time. Check: sudo journalctl -u k3s -n 200 --no-pager"
  exit 1
fi

# Put kubeconfig in the user's home for convenience on this machine.
echo "Configuring kubeconfig for current user..."
mkdir -p "$HOME/.kube"
sudo_if_needed cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo_if_needed chown "$(id -u)":"$(id -g)" "$HOME/.kube/config"

# Optional: open ufw if active
if command -v ufw >/dev/null 2>&1; then
  if sudo_if_needed ufw status | grep -q "Status: active"; then
    echo "UFW active: allowing 6443/tcp..."
    sudo_if_needed ufw allow 6443/tcp || true
  fi
fi

echo
echo "Server ready."
echo "Get the worker join token on this server with:"
echo "  sudo cat /var/lib/rancher/k3s/server/node-token"
echo "Verify on server:"
sudo_if_needed k3s kubectl get nodes -o wide
