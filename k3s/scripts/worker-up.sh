\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Worker up (join cluster) =="

apt_install curl ca-certificates

# Determine server host
CONF_DIR="/etc/k3s-lab"
CONF_HOST="${CONF_DIR}/server-host"
SERVER_HOST="k3s-server.local"

if [[ -f "$CONF_HOST" ]]; then
  SERVER_HOST="$(cat "$CONF_HOST" | tr -d '\r\n')"
fi

# If default doesn't resolve, prompt once and persist.
if ! getent hosts "$SERVER_HOST" >/dev/null 2>&1; then
  echo "Cannot resolve $SERVER_HOST."
  read -r -p "Enter the Ubuntu server IP (example 192.168.1.10): " ip
  SERVER_HOST="$ip"
  write_file_root "$CONF_HOST" "$SERVER_HOST"
fi

TOKEN_URL="http://${SERVER_HOST}:8088/agent-token"
echo "Fetching join token from: $TOKEN_URL"
TOKEN="$(curl -fsSL "$TOKEN_URL")"

if [[ -z "$TOKEN" ]]; then
  echo "Failed to fetch join token."
  exit 1
fi

if [[ ! -x /usr/local/bin/k3s ]]; then
  echo "Installing k3s agent..."
  curl -sfL https://get.k3s.io | sudo_if_needed K3S_URL="https://${SERVER_HOST}:6443" K3S_TOKEN="$TOKEN" sh -s - agent
else
  echo "k3s already installed; ensuring agent service is running..."
fi

sudo_if_needed systemctl enable --now k3s-agent

echo "Worker should now be joined. Verify on server: kubectl get nodes -o wide"
