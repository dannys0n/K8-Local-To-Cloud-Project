#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
PROJECT_KUBECONFIG="$ROOT_DIR/config/kubeconfig"

die() { echo "ERROR: $*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it first."; }

[[ -f "$ENV_FILE" ]] || die "Missing .env at $ENV_FILE"
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${SERVER_IP:?Set SERVER_IP in .env}"
: "${SERVER_SSH_USER:?Set SERVER_SSH_USER in .env}"
: "${PI_WORKER_IP:?Set PI_WORKER_IP in .env}"
: "${WORKER_SSH_USER:=pi}"
: "${SSH_KEY:=$HOME/.ssh/id_ed25519}"

SSH_KEY="${SSH_KEY/#\~/$HOME}"
[[ -f "$SSH_KEY" ]] || die "SSH key file does not exist: $SSH_KEY"

need_cmd ssh

uninstall_remote_node() {
  local host_user="$1"
  local host_ip="$2"
  local host_label="$3"

  echo "${host_label}: uninstalling k3s on ${host_user}@${host_ip}"
  ssh -T -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=5 \
    "${host_user}@${host_ip}" \
    "bash -s" <<'REMOTE'
set -euo pipefail

if ! sudo -n true >/dev/null 2>&1; then
  echo "ERROR: passwordless sudo required for cleanup"
  exit 1
fi

if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
  echo " - running k3s-agent-uninstall.sh"
  sudo /usr/local/bin/k3s-agent-uninstall.sh || true
fi

if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
  echo " - running k3s-uninstall.sh"
  sudo /usr/local/bin/k3s-uninstall.sh || true
fi

sudo systemctl stop k3s 2>/dev/null || true
sudo systemctl stop k3s-agent 2>/dev/null || true
sudo systemctl disable k3s 2>/dev/null || true
sudo systemctl disable k3s-agent 2>/dev/null || true

echo " - cleanup complete"
REMOTE
}

# Uninstall worker first, then server.
uninstall_remote_node "$WORKER_SSH_USER" "$PI_WORKER_IP" "worker"
uninstall_remote_node "$SERVER_SSH_USER" "$SERVER_IP" "server"

if [[ -L "$HOME/.kube/config" ]]; then
  linked_target="$(readlink -f "$HOME/.kube/config" 2>/dev/null || true)"
  if [[ "$linked_target" == "$PROJECT_KUBECONFIG" ]]; then
    rm -f "$HOME/.kube/config"
    echo "Removed kubeconfig symlink: $HOME/.kube/config"
  fi
fi

rm -f "$PROJECT_KUBECONFIG"
echo "Removed project kubeconfig: $PROJECT_KUBECONFIG"

echo "Cluster cleanup complete."
