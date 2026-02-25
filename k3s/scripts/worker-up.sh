#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Worker up (join cluster) =="

apt_install curl ca-certificates

CONF_DIR="/etc/k3s-lab"
CONF_FILE="${CONF_DIR}/worker.env"
K3S_URL="${K3S_URL:-}"
K3S_TOKEN="${K3S_TOKEN:-}"

# Reuse saved join info if available.
if [[ -f "$CONF_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONF_FILE"
fi

if [[ -z "${K3S_URL:-}" ]]; then
  read -r -p "Enter k3s server hostname or IP (example 192.168.1.10): " server_input
  server_input="$(echo "$server_input" | tr -d '\r\n[:space:]')"
  if [[ -z "$server_input" ]]; then
    echo "Server hostname/IP is required."
    exit 1
  fi

  if [[ "$server_input" == *"://"* ]]; then
    K3S_URL="$server_input"
  elif [[ "$server_input" == *":"* ]]; then
    K3S_URL="https://${server_input}"
  else
    K3S_URL="https://${server_input}:6443"
  fi
fi

if [[ -z "${K3S_TOKEN:-}" ]]; then
  echo "On the server, run: sudo cat /var/lib/rancher/k3s/server/node-token"
  read -r -p "Paste K3S_TOKEN: " K3S_TOKEN
  K3S_TOKEN="$(echo "$K3S_TOKEN" | tr -d '\r\n')"
fi

if [[ -z "${K3S_TOKEN:-}" ]]; then
  echo "K3S_TOKEN is required."
  exit 1
fi

printf -v conf_content 'K3S_URL=%q\nK3S_TOKEN=%q\n' "$K3S_URL" "$K3S_TOKEN"
write_file_root "$CONF_FILE" "$conf_content"
sudo_if_needed chmod 0600 "$CONF_FILE"

echo "Installing/updating k3s agent via https://get.k3s.io ..."
curl -sfL https://get.k3s.io | sudo_if_needed sh -s - agent --server "$K3S_URL" --token "$K3S_TOKEN"

if ! sudo_if_needed systemctl enable --now k3s-agent; then
  echo "k3s-agent failed to start. Diagnostics:"
  sudo_if_needed systemctl status k3s-agent --no-pager -l || true
  logs="$(sudo_if_needed journalctl -u k3s-agent -n 200 --no-pager || true)"
  printf "%s\n" "$logs"
  if printf "%s\n" "$logs" | grep -q "Failed to find memory cgroup"; then
    echo
    echo "Detected missing memory cgroup support."
    echo "Run: make worker-setup"
    echo "Then reboot the Pi and rerun: make worker-up"
  fi
  exit 1
fi

echo "Worker should now be joined."
echo "Verify on server: sudo k3s kubectl get nodes -o wide"
