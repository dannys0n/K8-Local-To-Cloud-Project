#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/config/cluster.env"
KUBECONFIG_PATH="$ROOT_DIR/config/kubeconfig"
CONTEXT_NAME="k3s-homelab"

die() { echo "ERROR: $*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it first."; }

print_usage() {
  cat <<EOF2
Usage:
  ./scripts/bootstrap.sh [options]

Options (CLI flags override config values):
  --server-ip <ip>             k3s server node IP (required)
  --server-user <user>         SSH user for the server node (required)
  --ssh-key <path>             SSH private key path (required)
  --use-sudo <true|false>      Use sudo on remote nodes (default: true)
  --worker-ip <ip>             Worker IP (repeatable)
  --worker-ips "<ip1 ip2>"      Space-separated worker IP list
  --worker-user <user>         SSH user for workers (default: pi)
  --api-endpoint <host-or-ip>  TLS SAN endpoint for kube-apiserver (default: server IP)
  --k3s-extra-args "<args>"     Extra args passed to k3s server install
  --k3s-version <version>      Pin k3s version (optional)
  --k3sup-version <version>    Pin k3sup installer version (optional)
  --help                       Show this help

Config fallback:
  If config/cluster.env exists, values are loaded from it.
  Any CLI option overrides config values.
EOF2
}

CLI_SERVER_IP=""
CLI_SERVER_SSH_USER=""
CLI_SSH_KEY=""
CLI_WORKER_SSH_USER=""
CLI_K3S_API_ENDPOINT=""
CLI_WORKER_IPS=""
CLI_K3S_EXTRA_ARGS=""
CLI_K3S_VERSION=""
CLI_K3SUP_VERSION=""
CLI_K3SUP_USE_SUDO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-ip)
      [[ $# -ge 2 ]] || die "--server-ip requires a value"
      CLI_SERVER_IP="$2"
      shift 2
      ;;
    --server-user)
      [[ $# -ge 2 ]] || die "--server-user requires a value"
      CLI_SERVER_SSH_USER="$2"
      shift 2
      ;;
    --ssh-key)
      [[ $# -ge 2 ]] || die "--ssh-key requires a value"
      CLI_SSH_KEY="$2"
      shift 2
      ;;
    --use-sudo)
      [[ $# -ge 2 ]] || die "--use-sudo requires a value (true|false)"
      CLI_K3SUP_USE_SUDO="$2"
      shift 2
      ;;
    --worker-ip)
      [[ $# -ge 2 ]] || die "--worker-ip requires a value"
      CLI_WORKER_IPS="${CLI_WORKER_IPS} $2"
      shift 2
      ;;
    --worker-ips)
      [[ $# -ge 2 ]] || die "--worker-ips requires a value"
      CLI_WORKER_IPS="$2"
      shift 2
      ;;
    --worker-user)
      [[ $# -ge 2 ]] || die "--worker-user requires a value"
      CLI_WORKER_SSH_USER="$2"
      shift 2
      ;;
    --api-endpoint)
      [[ $# -ge 2 ]] || die "--api-endpoint requires a value"
      CLI_K3S_API_ENDPOINT="$2"
      shift 2
      ;;
    --k3s-extra-args)
      [[ $# -ge 2 ]] || die "--k3s-extra-args requires a value"
      CLI_K3S_EXTRA_ARGS="$2"
      shift 2
      ;;
    --k3s-version)
      [[ $# -ge 2 ]] || die "--k3s-version requires a value"
      CLI_K3S_VERSION="$2"
      shift 2
      ;;
    --k3sup-version)
      [[ $# -ge 2 ]] || die "--k3sup-version requires a value"
      CLI_K3SUP_VERSION="$2"
      shift 2
      ;;
    --help|-h)
      print_usage
      exit 0
      ;;
    *)
      die "Unknown option: $1 (run with --help)"
      ;;
  esac
done

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

[[ -n "$CLI_SERVER_IP" ]] && SERVER_IP="$CLI_SERVER_IP"
[[ -n "$CLI_SERVER_SSH_USER" ]] && SERVER_SSH_USER="$CLI_SERVER_SSH_USER"
[[ -n "$CLI_SSH_KEY" ]] && SSH_KEY="$CLI_SSH_KEY"
[[ -n "$CLI_WORKER_SSH_USER" ]] && WORKER_SSH_USER="$CLI_WORKER_SSH_USER"
[[ -n "$CLI_WORKER_IPS" ]] && WORKER_IPS="$CLI_WORKER_IPS"
[[ -n "$CLI_K3S_API_ENDPOINT" ]] && K3S_API_ENDPOINT="$CLI_K3S_API_ENDPOINT"
[[ -n "$CLI_K3S_EXTRA_ARGS" ]] && K3S_EXTRA_ARGS="$CLI_K3S_EXTRA_ARGS"
[[ -n "$CLI_K3S_VERSION" ]] && K3S_VERSION="$CLI_K3S_VERSION"
[[ -n "$CLI_K3SUP_VERSION" ]] && K3SUP_VERSION="$CLI_K3SUP_VERSION"
[[ -n "$CLI_K3SUP_USE_SUDO" ]] && K3SUP_USE_SUDO="$CLI_K3SUP_USE_SUDO"

: "${SERVER_IP:?SERVER_IP is required (set in config/cluster.env or via --server-ip)}"
: "${SERVER_SSH_USER:?SERVER_SSH_USER is required (set in config/cluster.env or via --server-user)}"
: "${SSH_KEY:?SSH_KEY is required (set in config/cluster.env or via --ssh-key)}"
: "${WORKER_SSH_USER:=pi}"
: "${WORKER_IPS:=}"
: "${K3S_EXTRA_ARGS:=}"
: "${K3S_VERSION:=}"
: "${K3SUP_VERSION:=}"
: "${K3S_API_ENDPOINT:=$SERVER_IP}"
: "${K3SUP_USE_SUDO:=true}"

[[ "$K3SUP_USE_SUDO" == "true" || "$K3SUP_USE_SUDO" == "false" ]] || \
  die "K3SUP_USE_SUDO must be 'true' or 'false' (got: $K3SUP_USE_SUDO)"

SSH_KEY="${SSH_KEY/#\~/$HOME}"
[[ -f "$SSH_KEY" ]] || die "SSH key file does not exist: $SSH_KEY"

need_cmd ssh
need_cmd kubectl
# Helm is only required for platform.sh, not for bootstrap.
# k3sup can be auto-installed below.

check_ssh_access() {
  local host_ip="$1"
  local host_user="$2"

  if ! ssh -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=5 \
    "${host_user}@${host_ip}" true >/dev/null 2>&1; then
    die "SSH auth failed for ${host_user}@${host_ip}. Install your public key on that node first (example: ssh-copy-id -i ${SSH_KEY}.pub ${host_user}@${host_ip})"
  fi
}

check_sudo_access() {
  local host_ip="$1"
  local host_user="$2"

  [[ "$K3SUP_USE_SUDO" == "true" ]] || return 0

  if ! ssh -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=5 \
    "${host_user}@${host_ip}" 'sudo -n true' >/dev/null 2>&1; then
    die "Passwordless sudo is required for ${host_user}@${host_ip}. Run 'make sudo-setup', or set it manually in /etc/sudoers.d, or set K3SUP_USE_SUDO=false and use root SSH."
  fi
}

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

link_default_kubeconfig() {
  local default_dir="$HOME/.kube"
  local default_path="$default_dir/config"
  local backup_path=""

  mkdir -p "$default_dir"

  if [[ -e "$default_path" && ! -L "$default_path" ]]; then
    backup_path="${default_path}.bak.$(date +%Y%m%d%H%M%S)"
    mv "$default_path" "$backup_path"
    echo "Backed up existing kubeconfig: $backup_path"
  fi

  ln -sfn "$KUBECONFIG_PATH" "$default_path"
  chmod 600 "$KUBECONFIG_PATH" >/dev/null 2>&1 || true
  echo "Linked default kubeconfig: $default_path -> $KUBECONFIG_PATH"
}

echo "Checking SSH access to nodes ..."
check_ssh_access "$SERVER_IP" "$SERVER_SSH_USER"
for ip in $WORKER_IPS; do
  check_ssh_access "$ip" "$WORKER_SSH_USER"
done

echo "Checking sudo access on nodes ..."
check_sudo_access "$SERVER_IP" "$SERVER_SSH_USER"
for ip in $WORKER_IPS; do
  check_sudo_access "$ip" "$WORKER_SSH_USER"
done

install_k3sup_if_missing

echo "Installing k3s server on $SERVER_IP ..."
K3S_SERVER_ARGS="--write-kubeconfig-mode 644"
if [[ -n "$K3S_EXTRA_ARGS" ]]; then
  K3S_SERVER_ARGS+=" ${K3S_EXTRA_ARGS}"
fi

K3SUP_INSTALL_ARGS=(
  install
  --ip "$SERVER_IP"
  --user "$SERVER_SSH_USER"
  --ssh-key "$SSH_KEY"
  --tls-san "$K3S_API_ENDPOINT"
  --local-path "$KUBECONFIG_PATH"
  --context "$CONTEXT_NAME"
  --sudo "$K3SUP_USE_SUDO"
  --k3s-extra-args "$K3S_SERVER_ARGS"
)

if [[ -n "$K3S_VERSION" ]]; then
  K3SUP_INSTALL_ARGS+=(--k3s-version "$K3S_VERSION")
fi

mkdir -p "$(dirname "$KUBECONFIG_PATH")"
k3sup "${K3SUP_INSTALL_ARGS[@]}"

export KUBECONFIG="$KUBECONFIG_PATH"

echo
echo "Joining workers (if any) ..."
if [[ -z "${WORKER_IPS// }" ]]; then
  echo " - no workers configured"
else
  for ip in $WORKER_IPS; do
    echo " - worker: $ip"
    k3sup join \
      --server-ip "$SERVER_IP" \
      --server-user "$SERVER_SSH_USER" \
      --ip "$ip" \
      --user "$WORKER_SSH_USER" \
      --sudo "$K3SUP_USE_SUDO" \
      --ssh-key "$SSH_KEY"
  done
fi

link_default_kubeconfig

echo
echo "Cluster context: $CONTEXT_NAME"
kubectl config use-context "$CONTEXT_NAME" >/dev/null

echo
echo "Done. Verify with:"
echo "  export KUBECONFIG=\"$KUBECONFIG_PATH\""
echo "  # or use default: ~/.kube/config (auto-linked)"
echo "  kubectl get nodes -o wide"
