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
  scripts/orchestrate/teardown-agent-ssh.sh <worker-host-or-ip> [worker-host-or-ip ...]

Env/config:
  WORKER_SSH_USER  Optional. Default: pi

Notes:
  - Removes k3s agent and restores host settings (including iptables mode) using agent teardown.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

SSH_USER_DEFAULT="${WORKER_SSH_USER:-pi}"

BUNDLE_TGZ="/tmp/k3s-lab-scripts.tgz"
tar -C "$REPO_ROOT" -czf "$BUNDLE_TGZ" scripts

for worker in "$@"; do
  ssh_target="$worker"
  if [[ "$worker" != *@* ]]; then
    ssh_target="${SSH_USER_DEFAULT}@${worker}"
  fi

  echo "== Tearing down worker: $ssh_target =="

  remote_dir="/tmp/k3s-lab"
  scp -q "$BUNDLE_TGZ" "$ssh_target:$remote_dir.tgz"
  ssh "$ssh_target" "rm -rf '$remote_dir' && mkdir -p '$remote_dir' && tar -xzf '$remote_dir.tgz' -C '$remote_dir' && rm -f '$remote_dir.tgz'"

  ssh "$ssh_target" "sudo bash '$remote_dir/scripts/cluster/agent/teardown.sh'"

  echo "Done: $ssh_target"
  echo
done
