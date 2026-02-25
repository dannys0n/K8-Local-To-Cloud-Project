#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

script_start_time="$(date --iso-8601=seconds)"

mask_secret() {
  local value="$1"
  local len="${#value}"
  if [[ "$len" -le 8 ]]; then
    printf '********'
    return 0
  fi
  printf '%s****%s' "${value:0:4}" "${value:len-4:4}"
}

normalize_server_url() {
  local input="$1"
  if [[ "$input" == *"://"* ]]; then
    printf '%s\n' "$input"
  elif [[ "$input" == *":"* ]]; then
    printf 'https://%s\n' "$input"
  else
    printf 'https://%s:6443\n' "$input"
  fi
}

server_host_port_from_url() {
  local url="$1"
  local target host port
  target="${url#*://}"
  target="${target%%/*}"

  if [[ "$target" == \[*\]* ]]; then
    host="${target%%]*}]"
    host="${host#[}"
    port="${target##*:}"
    if [[ "$port" == "$target" ]]; then
      port="6443"
    fi
    printf '%s %s\n' "$host" "$port"
    return 0
  fi

  if [[ "$target" == *":"* ]]; then
    host="${target%:*}"
    port="${target##*:}"
  else
    host="$target"
    port="6443"
  fi
  printf '%s %s\n' "$host" "$port"
}

print_agent_diagnostics() {
  echo "k3s-agent diagnostics:"
  sudo_if_needed systemctl status k3s-agent --no-pager -l || true
  sudo_if_needed journalctl -u k3s-agent -n 200 --no-pager || true
}

wait_for_agent_healthy() {
  local tries="${1:-20}"
  for _ in $(seq 1 "$tries"); do
    if sudo_if_needed systemctl is-active --quiet k3s-agent; then
      return 0
    fi
    sleep 2
  done
  return 1
}

has_recent_agent_fatal_logs() {
  local logs
  logs="$(sudo_if_needed journalctl -u k3s-agent --since "$script_start_time" --no-pager 2>/dev/null || true)"
  if [[ -z "$logs" ]]; then
    return 1
  fi
  printf '%s\n' "$logs" | grep -Eiq 'level=fatal|node password rejected|token|certificate signed by unknown authority|failed to get CA certs|401 Unauthorized|connection refused|context deadline exceeded'
}

echo "== Worker up (join cluster) =="

apt_install curl ca-certificates

CONF_DIR="/etc/k3s-lab"
CONF_FILE="${CONF_DIR}/worker.env"
CLI_K3S_URL="${K3S_URL:-}"
CLI_K3S_TOKEN="${K3S_TOKEN:-}"
K3S_URL=""
K3S_TOKEN=""

# Reuse saved join info if available.
if [[ -f "$CONF_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONF_FILE"
fi

# Explicit env vars should override saved config.
if [[ -n "$CLI_K3S_URL" ]]; then
  K3S_URL="$CLI_K3S_URL"
fi
if [[ -n "$CLI_K3S_TOKEN" ]]; then
  K3S_TOKEN="$CLI_K3S_TOKEN"
fi

# Prompt before reusing saved config unless explicitly skipped.
if [[ -f "$CONF_FILE" && "${FORCE_PROMPT:-0}" != "1" && -n "${K3S_URL:-}" && -n "${K3S_TOKEN:-}" && -z "$CLI_K3S_URL" && -z "$CLI_K3S_TOKEN" ]]; then
  echo "Saved worker join config found in $CONF_FILE:"
  echo "  K3S_URL=$K3S_URL"
  echo "  K3S_TOKEN=$(mask_secret "$K3S_TOKEN")"
  read -r -p "Reuse saved config? [Y/n]: " reuse_saved
  if [[ "${reuse_saved:-}" =~ ^[Nn]$ ]]; then
    K3S_URL=""
    K3S_TOKEN=""
  fi
fi

if [[ -z "${K3S_URL:-}" ]]; then
  read -r -p "Enter k3s server hostname or IP (example 192.168.1.10): " server_input
  server_input="$(echo "$server_input" | tr -d '\r\n[:space:]')"
  if [[ -z "$server_input" ]]; then
    echo "Server hostname/IP is required."
    exit 1
  fi

  K3S_URL="$(normalize_server_url "$server_input")"
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

read -r server_host server_port < <(server_host_port_from_url "$K3S_URL")
if [[ -z "${server_host:-}" || -z "${server_port:-}" ]]; then
  echo "Failed to parse K3S_URL: $K3S_URL"
  exit 1
fi

echo "Using server: $K3S_URL"
echo "Using token: $(mask_secret "$K3S_TOKEN")"
echo "Checking connectivity to ${server_host}:${server_port} ..."
if ! curl -kSs --connect-timeout 5 --max-time 8 "https://${server_host}:${server_port}" >/dev/null; then
  echo "Cannot reach k3s server at ${server_host}:${server_port}."
  echo "Update server IP/token and retry:"
  echo "  FORCE_PROMPT=1 make worker-up"
  exit 1
fi

printf -v conf_content 'K3S_URL=%q\nK3S_TOKEN=%q\n' "$K3S_URL" "$K3S_TOKEN"
write_file_root "$CONF_FILE" "$conf_content"
sudo_if_needed chmod 0600 "$CONF_FILE"

echo "Installing/updating k3s agent via https://get.k3s.io ..."
curl -sfL https://get.k3s.io | sudo_if_needed sh -s - agent --server "$K3S_URL" --token "$K3S_TOKEN"

if ! sudo_if_needed systemctl enable --now k3s-agent; then
  echo "k3s-agent failed to start."
  print_agent_diagnostics
  if sudo_if_needed journalctl -u k3s-agent -n 200 --no-pager | grep -q "Failed to find memory cgroup"; then
    echo
    echo "Detected missing memory cgroup support."
    echo "Run: make worker-setup"
    echo "Then reboot the Pi and rerun: make worker-up"
  fi
  exit 1
fi

if ! wait_for_agent_healthy 20; then
  echo "k3s-agent did not become healthy."
  print_agent_diagnostics
  exit 1
fi

if has_recent_agent_fatal_logs; then
  echo "Detected recent join/auth/connect errors in k3s-agent logs."
  print_agent_diagnostics
  echo "Likely stale server IP/token. Retry with:"
  echo "  FORCE_PROMPT=1 make worker-up"
  exit 1
fi

echo "Worker agent is running and connected to server endpoint."
echo "Verify on server: sudo k3s kubectl get nodes -o wide"
