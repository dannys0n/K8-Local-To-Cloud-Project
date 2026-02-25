#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/../../lib/common.sh"

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

has_recent_agent_error_logs() {
  local logs
  logs="$(sudo_if_needed journalctl -u k3s-agent --since "$script_start_time" --no-pager 2>/dev/null || true)"
  if [[ -z "$logs" ]]; then
    return 1
  fi
  printf '%s\n' "$logs" | grep -Eiq 'level=fatal|node password rejected|certificate signed by unknown authority|failed to get CA certs|401 Unauthorized|connection refused|context deadline exceeded'
}

echo "== Agent up (join cluster) =="

apt_install curl ca-certificates

# If the agent is already installed, don't re-run the installer (and don't require a token).
if [[ "${FORCE_REINSTALL:-0}" != "1" ]] && sudo_if_needed systemctl list-unit-files --type=service | grep -q '^k3s-agent\.service'; then
  echo "k3s-agent appears installed; ensuring it is enabled and running ..."
  sudo_if_needed systemctl enable --now k3s-agent || true

  if ! wait_for_agent_healthy 10; then
    echo "k3s-agent is installed but not healthy."
    print_agent_diagnostics
    exit 1
  fi

  echo "k3s-agent is running."
  exit 0
fi

CONF_DIR="/etc/k3s-lab"
CONF_FILE_AGENT="${CONF_DIR}/agent.env"
CONF_FILE_LEGACY="${CONF_DIR}/worker.env"

CLI_K3S_URL="${K3S_URL:-}"
CLI_K3S_TOKEN="${K3S_TOKEN:-}"
K3S_URL=""
K3S_TOKEN=""

# Reuse saved join info if available.
if [[ -f "$CONF_FILE_AGENT" ]]; then
  # shellcheck disable=SC1090
  source "$CONF_FILE_AGENT"
elif [[ -f "$CONF_FILE_LEGACY" ]]; then
  # shellcheck disable=SC1090
  source "$CONF_FILE_LEGACY"
fi

# Explicit env vars should override saved config.
if [[ -n "$CLI_K3S_URL" ]]; then
  K3S_URL="$CLI_K3S_URL"
fi
if [[ -n "$CLI_K3S_TOKEN" ]]; then
  K3S_TOKEN="$CLI_K3S_TOKEN"
fi

# Always ask before reusing saved config (unless explicit env vars were provided or skipped).
if [[ ( -f "$CONF_FILE_AGENT" || -f "$CONF_FILE_LEGACY" ) && "${FORCE_PROMPT:-0}" != "1" && -z "$CLI_K3S_URL" && -z "$CLI_K3S_TOKEN" && ( -n "${K3S_URL:-}" || -n "${K3S_TOKEN:-}" ) ]]; then
  echo "Saved join config found:"
  echo "  K3S_URL=${K3S_URL:-<unset>}"
  echo "  K3S_TOKEN=$( [[ -n "${K3S_TOKEN:-}" ]] && mask_secret "$K3S_TOKEN" || echo '<unset>' )"
  read -r -p "Reuse saved config? [Y/n]: " reuse_saved
  if [[ "${reuse_saved:-}" =~ ^[Nn]$ ]]; then
    K3S_URL=""
    K3S_TOKEN=""
  fi
fi

if [[ -z "${K3S_URL:-}" ]]; then
  read -r -p "Enter k3s server hostname or DNS name (example k3s-api.lan): " server_input
  server_input="$(echo "$server_input" | tr -d '\r\n[:space:]')"
  if [[ -z "$server_input" ]]; then
    echo "Server hostname/DNS is required."
    exit 1
  fi

  K3S_URL="$(normalize_server_url "$server_input")"
fi

# Only required on first install/reinstall.
if [[ -z "${K3S_TOKEN:-}" ]]; then
  echo "Join token options (recommended order):"
  echo "  1) Short-lived bootstrap token: sudo k3s token create --ttl 30m"
  echo "  2) Agent token: sudo cat /var/lib/rancher/k3s/server/agent-token"
  echo "  3) Node token (legacy): sudo cat /var/lib/rancher/k3s/server/node-token"
  read -r -p "Paste token: " K3S_TOKEN
  K3S_TOKEN="$(echo "$K3S_TOKEN" | tr -d '\r\n')"
fi

if [[ -z "${K3S_TOKEN:-}" ]]; then
  echo "Join token is required for installation."
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
  echo "Retry with: FORCE_PROMPT=1 make agent-up"
  exit 1
fi

# Cache only what you need. The join token is only required for (re)install.
# Set CACHE_JOIN_TOKEN=1 if you still want it saved.
mkdir_root "$CONF_DIR"
conf_content="K3S_URL=$(printf %q "$K3S_URL")\n"
if [[ "${CACHE_JOIN_TOKEN:-0}" == "1" ]]; then
  conf_content+="K3S_TOKEN=$(printf %q "$K3S_TOKEN")\n"
fi
write_file_root "$CONF_FILE_AGENT" "$conf_content"
sudo_if_needed chmod 0600 "$CONF_FILE_AGENT"

echo "Installing/updating k3s agent via https://get.k3s.io ..."
curl -sfL https://get.k3s.io | sudo_if_needed sh -s - agent --server "$K3S_URL" --token "$K3S_TOKEN"

if ! sudo_if_needed systemctl enable --now k3s-agent; then
  echo "k3s-agent failed to start."
  print_agent_diagnostics
  if sudo_if_needed journalctl -u k3s-agent -n 200 --no-pager | grep -q "Failed to find memory cgroup"; then
    echo
    echo "Detected missing memory cgroup support."
    echo "Run: make agent-setup"
    echo "Then reboot and rerun: make agent-up"
  fi
  exit 1
fi

if ! wait_for_agent_healthy 20; then
  echo "k3s-agent did not become healthy."
  print_agent_diagnostics
  exit 1
fi

# Avoid false negatives: only treat recent errors as fatal if the agent isn't healthy.
if has_recent_agent_error_logs && ! sudo_if_needed systemctl is-active --quiet k3s-agent; then
  echo "Detected recent join/auth/connect errors in k3s-agent logs."
  print_agent_diagnostics
  echo "Likely stale server address/token. Retry with: FORCE_PROMPT=1 make agent-up"
  exit 1
fi

echo "Agent is running and connected to server endpoint."
echo "Verify on server: sudo k3s kubectl get nodes -o wide"
