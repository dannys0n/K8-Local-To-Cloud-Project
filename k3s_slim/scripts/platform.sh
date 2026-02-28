#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/config/cluster.env"
ENV_FILE="$ROOT_DIR/.env"
KUBECONFIG_PATH="$ROOT_DIR/config/kubeconfig"

die() { echo "ERROR: $*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it first."; }

if [[ ! -f "$KUBECONFIG_PATH" ]]; then
  die "Missing kubeconfig: $KUBECONFIG_PATH (run ./scripts/bootstrap.sh first)"
fi

# Optional config sources:
# - .env is the primary project config
# - config/cluster.env remains supported for legacy/override use
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

: "${INSTALL_METRICS_SERVER:=true}"
: "${INSTALL_METALLB:=false}"
: "${METALLB_ADDRESS_POOL:=}"
: "${INSTALL_INGRESS_NGINX:=false}"
: "${INSTALL_CERT_MANAGER:=false}"

need_cmd kubectl
need_cmd helm

export KUBECONFIG="$KUBECONFIG_PATH"

# Namespaces
kubectl create namespace platform >/dev/null 2>&1 || true

install_metrics_server() {
  # Lightweight metrics server for `kubectl top`.
  helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
  helm repo update >/dev/null

  local release_exists="false"
  local sa_exists="false"

  if helm status metrics-server --namespace kube-system >/dev/null 2>&1; then
    release_exists="true"
  fi

  # k3s often ships metrics-server by default. If it already exists but is not
  # managed by this Helm release, keep the existing install and skip.
  if [[ "$release_exists" == "false" ]] && \
    kubectl get deployment metrics-server --namespace kube-system >/dev/null 2>&1; then
    echo "   metrics-server already exists in kube-system (non-Helm); skipping Helm install"
    return 0
  fi

  if kubectl get serviceaccount metrics-server --namespace kube-system >/dev/null 2>&1; then
    sa_exists="true"
  fi

  local helm_args=(
    upgrade
    --install
    metrics-server
    metrics-server/metrics-server
    --namespace
    kube-system
    --set
    args="{--kubelet-insecure-tls,--kubelet-preferred-address-types=InternalIP\,ExternalIP\,Hostname}"
    --wait
  )

  # If ServiceAccount already exists and release is new, don't ask Helm to own SA.
  if [[ "$release_exists" == "false" && "$sa_exists" == "true" ]]; then
    helm_args+=(--set serviceAccount.create=false --set serviceAccount.name=metrics-server)
  fi

  # k3s often needs insecure TLS from kubelets in homelab environments.
  helm "${helm_args[@]}"
}

install_metallb() {
  [[ -n "$METALLB_ADDRESS_POOL" ]] || die "INSTALL_METALLB=true but METALLB_ADDRESS_POOL is empty"

  helm repo add metallb https://metallb.github.io/metallb >/dev/null
  helm repo update >/dev/null

  kubectl create namespace metallb-system >/dev/null 2>&1 || true
  helm upgrade --install metallb metallb/metallb --namespace metallb-system --wait

  # Address pool (Layer2) - minimal for homelab.
  cat <<EOF | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: default-pool
  namespace: metallb-system
spec:
  addresses:
  - ${METALLB_ADDRESS_POOL}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: default
  namespace: metallb-system
spec: {}
EOF
}

install_ingress_nginx() {
  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null
  helm repo update >/dev/null

  kubectl create namespace ingress-nginx >/dev/null 2>&1 || true
  helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx     --namespace ingress-nginx     --wait
}

install_cert_manager() {
  helm repo add jetstack https://charts.jetstack.io >/dev/null
  helm repo update >/dev/null

  kubectl create namespace cert-manager >/dev/null 2>&1 || true
  helm upgrade --install cert-manager jetstack/cert-manager     --namespace cert-manager     --set crds.enabled=true     --wait
}

echo "Installing platform add-ons (safe to re-run) ..."
if [[ "$INSTALL_METRICS_SERVER" == "true" ]]; then
  echo " - metrics-server"
  install_metrics_server
fi

if [[ "$INSTALL_METALLB" == "true" ]]; then
  echo " - MetalLB"
  install_metallb
fi

if [[ "$INSTALL_INGRESS_NGINX" == "true" ]]; then
  echo " - ingress-nginx"
  install_ingress_nginx
fi

if [[ "$INSTALL_CERT_MANAGER" == "true" ]]; then
  echo " - cert-manager"
  install_cert_manager
fi

echo
echo "Done. Current pods:"
kubectl get pods -A | head -n 50
