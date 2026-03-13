#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "${SCRIPT_DIR}/lib.sh"

load_env

: "${CLUSTER_NAME:=template-dev}"
: "${K3D_SERVER_COUNT:=1}"
: "${K3D_AGENT_COUNT:=0}"

need_cmd docker
need_cmd k3d
need_cmd kubectl

docker info >/dev/null 2>&1 || die "docker daemon is not reachable"

if k3d cluster get "$CLUSTER_NAME" >/dev/null 2>&1; then
  log "k3d cluster '$CLUSTER_NAME' already exists"
else
  log "Creating k3d cluster '$CLUSTER_NAME' with ${K3D_SERVER_COUNT} server(s) and ${K3D_AGENT_COUNT} agent(s)"
  k3d cluster create "$CLUSTER_NAME" \
    --servers "$K3D_SERVER_COUNT" \
    --agents "$K3D_AGENT_COUNT" \
    --wait
fi

log "Merging kubeconfig for '$CLUSTER_NAME'"
k3d kubeconfig merge "$CLUSTER_NAME" --kubeconfig-switch-context
kubectl get nodes -o wide
