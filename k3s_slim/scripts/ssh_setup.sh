#!/usr/bin/env bash
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install openssh-client."; }

: "${SERVER_IP:?Set SERVER_IP in .env or pass SERVER_IP=...}"
: "${SERVER_SSH_USER:?Set SERVER_SSH_USER in .env or pass SERVER_SSH_USER=...}"
: "${PI_WORKER_IP:?Set PI_WORKER_IP in .env or pass PI_WORKER_IP=...}"
: "${WORKER_SSH_USER:=pi}"
: "${SSH_KEY:=$HOME/.ssh/id_ed25519}"

SSH_KEY="${SSH_KEY/#\~/$HOME}"
KEY_DIR="$(dirname "$SSH_KEY")"

need_cmd ssh
need_cmd ssh-keygen
need_cmd ssh-copy-id

mkdir -p "$KEY_DIR"
if [[ -f "$SSH_KEY" ]]; then
  echo "SSH key already exists: $SSH_KEY"
else
  echo "Creating SSH key: $SSH_KEY"
  ssh-keygen -t ed25519 -a 100 -f "$SSH_KEY" -N "" -C "k3s-homelab"
fi

ensure_key_access() {
  local user="$1"
  local ip="$2"
  local label="$3"

  echo "Ensuring key access to ${label}: ${user}@${ip}"
  if ssh -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=5 \
    "${user}@${ip}" true >/dev/null 2>&1; then
    echo " - ${label} key auth already works"
  else
    ssh-copy-id -i "${SSH_KEY}.pub" -o StrictHostKeyChecking=accept-new "${user}@${ip}"
  fi
}

ensure_key_access "$SERVER_SSH_USER" "$SERVER_IP" "server"
ensure_key_access "$WORKER_SSH_USER" "$PI_WORKER_IP" "worker"

echo "SSH setup done. Run 'make linux-pi'."
