#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/config/cluster.env"
KUBECONFIG_PATH="$ROOT_DIR/config/kubeconfig"
CONTEXT_NAME="k3s-homelab"

die() { echo "ERROR: $*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it first."; }

if [[ ! -f "$CONFIG_FILE" ]]; then
  die "Missing config file: $CONFIG_FILE (copy config/cluster.env.example to config/cluster.env)"
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${SERVER_IP:?SERVER_IP is required}"
: "${SERVER_SSH_USER:?SERVER_SSH_USER is required}"
: "${SSH_KEY:?SSH_KEY is required}"
: "${K3S_API_ENDPOINT:?K3S_API_ENDPOINT is required}"
: "${WORKER_SSH_USER:=pi}"
: "${WORKER_IPS:=}"
: "${K3S_EXTRA_ARGS:=}"
: "${K3S_VERSION:=}"
: "${K3SUP_VERSION:=}"

need_cmd ssh
need_cmd kubectl
# Helm is only required for platform.sh, not for bootstrap.
# k3sup can be auto-installed below.

install_k3sup_if_missing() {
  if command -v k3sup >/dev/null 2>&1; then
    return 0
  fi

  echo "k3sup not found; installing to ~/.local/bin (requires curl)..."
  need_cmd curl
  mkdir -p "$HOME/.local/bin"
  export PATH="$HOME/.local/bin:$PATH"

  # Official installer script from the k3sup project (convenient for learning/homelab).
  # If you prefer, install a pinned binary manually and remove this.
  if [[ -n "$K3SUP_VERSION" ]]; then
    curl -sLS https://get.k3sup.dev | sh -s -- -b "$HOME/.local/bin" "$K3SUP_VERSION"
  else
    curl -sLS https://get.k3sup.dev | sh -s -- -b "$HOME/.local/bin"
  fi

  command -v k3sup >/dev/null 2>&1 || die "k3sup install failed"
}

install_k3sup_if_missing

echo "Installing k3s server on $SERVER_IP ..."
K3SUP_INSTALL_ARGS=(
  install
  --ip "$SERVER_IP"
  --user "$SERVER_SSH_USER"
  --ssh-key "$SSH_KEY"
  --tls-san "$K3S_API_ENDPOINT"
  --local-path "$KUBECONFIG_PATH"
  --context "$CONTEXT_NAME"
  --merge
  --k3s-extra-args "--write-kubeconfig-mode 644 ${K3S_EXTRA_ARGS}"
)

if [[ -n "$K3S_VERSION" ]]; then
  K3SUP_INSTALL_ARGS+=(--k3s-version "$K3S_VERSION")
fi

k3sup "${K3SUP_INSTALL_ARGS[@]}"

export KUBECONFIG="$KUBECONFIG_PATH"

echo
echo "Joining workers (if any) ..."
for ip in $WORKER_IPS; do
  echo " - worker: $ip"
  k3sup join     --server-ip "$SERVER_IP"     --ip "$ip"     --user "$WORKER_SSH_USER"     --ssh-key "$SSH_KEY"
done

echo
echo "Cluster context: $CONTEXT_NAME"
kubectl config use-context "$CONTEXT_NAME" >/dev/null

echo
echo "Done. Verify with:"
echo "  export KUBECONFIG="$KUBECONFIG_PATH""
echo "  kubectl get nodes -o wide"
