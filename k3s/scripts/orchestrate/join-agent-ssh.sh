#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/common.sh"

CONFIG_FILE="$REPO_ROOT/config/cluster.env"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

usage() {
  cat <<USAGE
Usage:
  scripts/orchestrate/join-agent-ssh.sh <worker-host-or-ip> [worker-host-or-ip ...]

Env/config:
  K3S_API_ENDPOINT   Required. DNS name for the k3s server (example: k3s-api.lan)
  K3S_BOOTSTRAP_TTL  Optional. Default: 30m
  WORKER_SSH_USER    Optional. Default: pi

Notes:
  - Run this from the k3s server (or any machine with sudo access to run: k3s token create).
  - Requires SSH access to workers and sudo on the workers.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

: "${K3S_API_ENDPOINT:?Set K3S_API_ENDPOINT in config/cluster.env (or env) to a stable DNS name for your server.}"
K3S_URL="https://${K3S_API_ENDPOINT}:6443"
TTL="${K3S_BOOTSTRAP_TTL:-30m}"
SSH_USER_DEFAULT="${WORKER_SSH_USER:-pi}"

if ! command -v k3s >/dev/null 2>&1; then
  echo "k3s binary not found on this machine. Run this script on the k3s server (or install k3s here)."
  exit 1
fi

# Bundle scripts so relative paths continue to work on the worker.
BUNDLE_TGZ="/tmp/k3s-lab-scripts.tgz"
tar -C "$REPO_ROOT" -czf "$BUNDLE_TGZ" scripts

for worker in "$@"; do
  ssh_target="$worker"
  if [[ "$worker" != *@* ]]; then
    ssh_target="${SSH_USER_DEFAULT}@${worker}"
  fi

  echo "== Joining worker: $ssh_target =="

  echo "Creating bootstrap token (ttl=${TTL}) ..."
  bootstrap_token="$(sudo_if_needed k3s token create --ttl "$TTL" --description "bootstrap for $ssh_target" | tr -d '\r\n')"
  if [[ -z "$bootstrap_token" ]]; then
    echo "Failed to create bootstrap token."
    exit 1
  fi

  remote_dir="/tmp/k3s-lab"

  echo "Copying scripts bundle ..."
  scp -q "$BUNDLE_TGZ" "$ssh_target:$remote_dir.tgz"

  echo "Extracting scripts bundle ..."
  ssh "$ssh_target" "rm -rf '$remote_dir' && mkdir -p '$remote_dir' && tar -xzf '$remote_dir.tgz' -C '$remote_dir' && rm -f '$remote_dir.tgz'"

  echo "Running agent setup ..."
  ssh "$ssh_target" "sudo bash '$remote_dir/scripts/cluster/agent/setup.sh'"

  echo "Installing/joining agent ..."
  ssh "$ssh_target" "sudo env K3S_URL='$K3S_URL' K3S_TOKEN='$bootstrap_token' CACHE_JOIN_TOKEN=0 bash '$remote_dir/scripts/cluster/agent/up.sh'"

  echo
  echo "Done: $ssh_target"
  echo "Verify on server: sudo k3s kubectl get nodes -o wide"
  echo

done
