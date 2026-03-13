#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../../common/lib.sh
source "${TIER_ROOT}/../common/lib.sh"

load_env

: "${INSTALL_METALLB:=true}"
: "${METALLB_NAMESPACE:=metallb-system}"
: "${METALLB_CHART_VERSION:=}"
: "${BASE_STACK_RELEASE:=base-stack}"
: "${BASE_STACK_NAMESPACE:=base-stack}"
: "${BASE_STACK_VALUES:=}"
: "${KUBECONFIG_PATH:=$(default_kubeconfig_path)}"

need_cmd kubectl
need_cmd helm

activate_kubeconfig_if_present "$KUBECONFIG_PATH"

if [[ "$INSTALL_METALLB" == "true" ]]; then
  log "Installing MetalLB into namespace '$METALLB_NAMESPACE'"
  helm repo add metallb https://metallb.github.io/metallb >/dev/null
  helm repo update >/dev/null

  metallb_args=(
    upgrade
    --install
    metallb
    metallb/metallb
    --namespace "$METALLB_NAMESPACE"
    --create-namespace
    --wait
  )

  if [[ -n "$METALLB_CHART_VERSION" ]]; then
    metallb_args+=(--version "$METALLB_CHART_VERSION")
  fi

  helm "${metallb_args[@]}"
else
  log "Skipping MetalLB chart install because INSTALL_METALLB=$INSTALL_METALLB"
fi

CHART_DIR="${TIER_ROOT}/charts/base-stack"
[[ -d "$CHART_DIR" ]] || die "Missing chart directory: $CHART_DIR"

helm_args=(
  upgrade
  --install
  "$BASE_STACK_RELEASE"
  "$CHART_DIR"
  --namespace "$BASE_STACK_NAMESPACE"
  --create-namespace
  --wait
)

if [[ -n "$BASE_STACK_VALUES" ]]; then
  helm_args+=(-f "$BASE_STACK_VALUES")
fi

log "Deploying Helm release '$BASE_STACK_RELEASE' into namespace '$BASE_STACK_NAMESPACE'"
helm "${helm_args[@]}"
kubectl -n "$BASE_STACK_NAMESPACE" get pods,svc
