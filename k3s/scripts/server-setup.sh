\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Server setup (Ubuntu server) =="

# Dependencies (mDNS + token HTTP server)
apt_install curl ca-certificates gnupg tar python3 avahi-daemon libnss-mdns

# Ensure mDNS is running (for k3s-server.local)
sudo_if_needed systemctl enable --now avahi-daemon || true

# Set stable hostname for .local discovery
CURRENT_HOST="$(hostname)"
if [[ "$CURRENT_HOST" != "k3s-server" ]]; then
  echo "Setting hostname to k3s-server (for k3s-server.local)..."
  sudo_if_needed hostnamectl set-hostname k3s-server
fi

# Install k3s server (also acts as a worker)
if [[ ! -x /usr/local/bin/k3s ]]; then
  echo "Installing k3s server..."
  curl -sfL https://get.k3s.io | sudo_if_needed sh -s - server \
    --write-kubeconfig-mode 644 \
    --tls-san k3s-server.local
else
  echo "k3s already installed."
fi

# Ensure service is up
sudo_if_needed systemctl enable --now k3s

# Ensure kubectl exists (k3s normally provides it; create symlink if missing)
if ! command -v kubectl >/dev/null 2>&1; then
  if [[ -x /usr/local/bin/k3s ]]; then
    sudo_if_needed ln -sf /usr/local/bin/k3s /usr/local/bin/kubectl
  fi
fi

echo "Waiting for k3s API to be ready..."
if ! wait_for_k3s 90; then
  echo "k3s did not become ready in time. Check: sudo journalctl -u k3s -n 200 --no-pager"
  exit 1
fi

# Allow scheduling on server (server implicitly acts as a worker)
echo "Allowing scheduling on server (untaint control-plane)..."
kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>/dev/null || true
kubectl taint nodes --all node-role.kubernetes.io/master- 2>/dev/null || true

# Put kubeconfig in user's home pointing at k3s-server.local
echo "Configuring kubeconfig for current user..."
mkdir -p "$HOME/.kube"
sudo_if_needed cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo_if_needed chown "$(id -u)":"$(id -g)" "$HOME/.kube/config"
sed -i.bak 's/127.0.0.1/k3s-server.local/g' "$HOME/.kube/config" || true

# Prepare token share directory
echo "Preparing join-token HTTP endpoint (LAN only)..."
sudo_if_needed mkdir -p /opt/k3s-lab
sudo_if_needed sh -c "cat /var/lib/rancher/k3s/server/node-token > /opt/k3s-lab/agent-token"
sudo_if_needed chmod 0644 /opt/k3s-lab/agent-token
sudo_if_needed sh -c "echo k3s-server.local > /opt/k3s-lab/server-host"

# Create systemd unit for HTTP token server
UNIT_PATH="/etc/systemd/system/k3s-lab-token.service"
UNIT_CONTENT="[Unit]
Description=k3s-lab token HTTP server (INSECURE, LAN only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/k3s-lab
ExecStart=/usr/bin/python3 -m http.server 8088 --bind 0.0.0.0
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"
write_file_root "$UNIT_PATH" "$UNIT_CONTENT"

sudo_if_needed systemctl daemon-reload
sudo_if_needed systemctl enable --now k3s-lab-token

# Optional: open ufw if active
if command -v ufw >/dev/null 2>&1; then
  if sudo_if_needed ufw status | grep -q "Status: active"; then
    echo "UFW active: allowing 6443/tcp and 8088/tcp..."
    sudo_if_needed ufw allow 6443/tcp || true
    sudo_if_needed ufw allow 8088/tcp || true
  fi
fi

echo
echo "Server ready."
echo "Worker join token URL (LAN only): http://k3s-server.local:8088/agent-token"
echo "Verify on server: kubectl get nodes -o wide"
kubectl get nodes -o wide
