#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "${SCRIPT_DIR}/lib.sh"

load_env

: "${SERVER_IPS:?Set SERVER_IPS in .env}"
: "${SERVER_SSH_USER:?Set SERVER_SSH_USER in .env}"
: "${SSH_KEY:=~/.ssh/id_ed25519}"
: "${K3SUP_USE_SUDO:=true}"
: "${K3S_CONTEXT_NAME:=template-k3s}"
: "${KUBECONFIG_PATH:=$(default_kubeconfig_path)}"
: "${WORKER_SSH_USER:=${AGENT_SSH_USER:-}}"
: "${WORKER_IPS:=${AGENT_IPS:-}}"

need_cmd ssh
need_cmd kubectl
need_cmd k3sup

primary_server_ip="$(first_token "$SERVER_IPS")"
[[ -n "$primary_server_ip" ]] || die "SERVER_IPS did not contain a usable primary server IP"

ssh_key_path="$(resolve_home_path "$SSH_KEY")"
[[ -f "$ssh_key_path" ]] || die "SSH key does not exist: $ssh_key_path"

ensure_parent_dir "$KUBECONFIG_PATH"

log "Installing k3s on ${SERVER_SSH_USER}@${primary_server_ip} with k3sup"
k3sup install \
  --ip "$primary_server_ip" \
  --user "$SERVER_SSH_USER" \
  --ssh-key "$ssh_key_path" \
  --local-path "$KUBECONFIG_PATH" \
  --context "$K3S_CONTEXT_NAME" \
  --sudo "$K3SUP_USE_SUDO"

if [[ -n "${WORKER_IPS// }" ]]; then
  [[ -n "$WORKER_SSH_USER" ]] || die "WORKER_SSH_USER is required when WORKER_IPS is set"
  read -r -a worker_ips <<< "${WORKER_IPS}"

  log "Joining ${#worker_ips[@]} worker node(s)"
  for worker_ip in "${worker_ips[@]}"; do
    log "Joining ${WORKER_SSH_USER}@${worker_ip}"
    k3sup join \
      --ip "$worker_ip" \
      --user "$WORKER_SSH_USER" \
      --server-ip "$primary_server_ip" \
      --server-user "$SERVER_SSH_USER" \
      --ssh-key "$ssh_key_path" \
      --sudo "$K3SUP_USE_SUDO"
  done
fi

export KUBECONFIG="$KUBECONFIG_PATH"
kubectl config use-context "$K3S_CONTEXT_NAME" >/dev/null || true
kubectl get nodes -o wide
