#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/infra/eks"
RUNTIME="$ROOT/.generated/eks"
ACTION="${1:-status}"
IMAGE_TAG="${2:-}"

need() { command -v "$1" >/dev/null || { echo "Required command '$1' was not found on PATH." >&2; exit 1; }; }
tfout() { terraform -chdir="$TF_DIR" output -raw "$1"; }

infra_up() {
  need terraform
  need aws
  terraform -chdir="$TF_DIR" init
  terraform -chdir="$TF_DIR" apply
  aws eks update-kubeconfig --name "$(tfout cluster_name)" --region "$(tfout aws_region)"
}

resolve_tag() {
  if [[ -n "$IMAGE_TAG" ]]; then printf '%s' "$IMAGE_TAG"; return; fi
  printf '%s-%s' "$(git -C "$ROOT" rev-parse --short=12 HEAD)" "$(date -u +%Y%m%d%H%M%S)"
}

push_images() {
  local tag="$1" region server gateway autoscaler registry
  need docker
  region="$(tfout aws_region)"
  server="$(tfout ecr_server_repository)"
  gateway="$(tfout ecr_gateway_repository)"
  autoscaler="$(tfout ecr_autoscaler_repository)"
  registry="${server%%/*}"
  aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"
  docker buildx build --platform linux/amd64,linux/arm64 --push -t "$server:$tag" "$ROOT/app/server"
  docker buildx build --platform linux/amd64,linux/arm64 --push -t "$gateway:$tag" "$ROOT/app/gateway"
  docker buildx build --platform linux/amd64,linux/arm64 --push -t "$autoscaler:$tag" "$ROOT/infra/autoscaler"
}

write_runtime() {
  local tag="$1" server gateway autoscaler
  server="$(tfout ecr_server_repository)"
  gateway="$(tfout ecr_gateway_repository)"
  autoscaler="$(tfout ecr_autoscaler_repository)"
  mkdir -p "$RUNTIME"
  {
    printf '%s\n' 'apiVersion: kustomize.config.k8s.io/v1beta1' 'kind: Kustomization' 'resources:' '  - ../../deploy/overlays/eks' 'images:'
    printf '  - name: simple-tcp-server\n    newName: %s\n    newTag: %s\n' "$server" "$tag"
    printf '  - name: tcp-gateway\n    newName: %s\n    newTag: %s\n' "$gateway" "$tag"
    printf '  - name: tcp-server-autoscaler\n    newName: %s\n    newTag: %s\n' "$autoscaler" "$tag"
    printf '%s\n' 'patches:' '  - target:' '      kind: Service' '      name: gateway' '    patch: |-' '      - op: add' '        path: /spec/loadBalancerSourceRanges' '        value:'
    tr ',' '\n' <<<"$(tfout nlb_source_cidrs_csv)" | sed 's/^/          - /'
  } > "$RUNTIME/kustomization.yaml"
}

deploy() {
  local tag="$1" address
  need kubectl
  address="$(tfout valkey_address)"
  kubectl apply -f "$ROOT/deploy/base/namespace.yaml"
  kubectl create secret generic tcp-server-valkey -n tcp-lab --from-literal="addresses=$address" --from-literal="tls=true" --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -k "$ROOT/infra/autoscaler/prometheus"
  write_runtime "$tag"
  kubectl apply -k "$RUNTIME"
}

case "$ACTION" in
  infra-up) infra_up ;;
  push) tag="$(resolve_tag)"; push_images "$tag"; echo "Pushed immutable image tag: $tag" ;;
  deploy) [[ -n "$IMAGE_TAG" ]] || { echo 'deploy requires an image tag as the second argument.' >&2; exit 1; }; deploy "$IMAGE_TAG" ;;
  up) infra_up; tag="$(resolve_tag)"; push_images "$tag"; deploy "$tag"; echo "EKS lab deployed with image tag: $tag" ;;
  status) need kubectl; kubectl get nodes; kubectl get pods,service,pdb -n tcp-lab -o wide ;;
  down) need terraform; if [[ -f "$RUNTIME/kustomization.yaml" ]]; then kubectl delete -k "$RUNTIME" --ignore-not-found=true || true; fi; terraform -chdir="$TF_DIR" destroy ;;
  *) echo "Usage: scripts/eks.sh {infra-up|push|deploy TAG|up|status|down}" >&2; exit 2 ;;
esac
