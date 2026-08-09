#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="tcp-lab"
SERVER_IMAGE="simple-tcp-server:dev"
GATEWAY_IMAGE="tcp-gateway:dev"
AUTOSCALER_IMAGE="tcp-server-autoscaler:dev"
LOADGEN_IMAGE="tcp-loadgen:dev"
VALKEY_IMAGE="valkey/valkey:8.1-alpine"

for command in docker kind kubectl; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command not found: $command" >&2; exit 1; }
done

cd "$ROOT"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "Creating kind cluster '$CLUSTER'..."
  kind create cluster --config infra/kind/cluster.yaml
else
  echo "kind cluster '$CLUSTER' already exists."
fi

while read -r node; do
  [[ -z "$node" ]] && continue
  kubectl taint node "$node" tcp-lab.io/database- 2>/dev/null || true
  kubectl label node "$node" tcp-lab.io/database- 2>/dev/null || true
done < <(kubectl get nodes -l tcp-lab.io/database=true -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')

echo "Building application and load-generator images..."
docker build -t "$SERVER_IMAGE" app/server
docker build -t "$GATEWAY_IMAGE" app/gateway
docker build --provenance=false -t "$AUTOSCALER_IMAGE" infra/autoscaler
docker build -t "$LOADGEN_IMAGE" tools/loadgen
docker build --provenance=false -t prom/prometheus:v3.13.1 infra/autoscaler/prometheus
docker build --provenance=false -t "$VALKEY_IMAGE" infra/valkey

echo "Loading image into kind..."
kind load docker-image "$SERVER_IMAGE" "$GATEWAY_IMAGE" "$AUTOSCALER_IMAGE" prom/prometheus:v3.13.1 "$VALKEY_IMAGE" --name "$CLUSTER"

echo "Installing autoscaling dependencies..."
bash "$ROOT/infra/autoscaler/install-kind.sh"

echo "Applying Kubernetes resources..."
kubectl apply -f deploy/base/namespace.yaml
kubectl delete job/valkey-cluster-init -n tcp-lab --ignore-not-found
kubectl apply -k deploy/overlays/kind
kubectl rollout status statefulset/valkey -n tcp-lab --timeout=180s
kubectl wait --for=condition=Complete job/valkey-cluster-init -n tcp-lab --timeout=180s
kubectl rollout restart deployment/tcp-server -n tcp-lab
kubectl rollout restart deployment/gateway -n tcp-lab
kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s
kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
kubectl rollout status deployment/gateway-autoscaler -n tcp-lab --timeout=180s
kubectl rollout status deployment/tcp-server-autoscaler -n tcp-lab --timeout=180s

echo
echo "Ready."
echo "TCP endpoint: 127.0.0.1:9000"
echo "Gateway stats: http://127.0.0.1:8404/"
echo "Map client: python3 tools/client.py"
