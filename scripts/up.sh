#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="tcp-lab"
SERVER_IMAGE="simple-tcp-server:dev"
GATEWAY_IMAGE="tcp-gateway:dev"
AUTOSCALER_IMAGE="tcp-server-autoscaler:dev"
LOADGEN_IMAGE="tcp-loadgen:dev"
VALKEY_AUTOSCALER_IMAGE="valkey-shard-autoscaler:dev"

for command in docker kind kubectl helm; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command not found: $command" >&2; exit 1; }
done

cd "$ROOT"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "Creating kind cluster '$CLUSTER'..."
  kind create cluster --config infra/kind/cluster.yaml
else
  echo "kind cluster '$CLUSTER' already exists."
fi

database_nodes="$(kubectl get nodes -l tcp-lab.io/database=true -o name)"
database_node_count="$(printf '%s\n' "$database_nodes" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$database_node_count" -ne 3 ]]; then
  echo "Cluster topology is outdated: expected 3 dedicated database workers, found $database_node_count. Recreate the kind cluster." >&2
  exit 1
fi
if [[ -n "$(kubectl get namespace/tcp-lab --ignore-not-found -o name)" ]] && \
   [[ -n "$(kubectl get statefulset/valkey -n tcp-lab --ignore-not-found -o name)" ]]; then
  echo "The cluster contains the retired hand-managed Valkey StatefulSet. Recreate the kind cluster before installing the operator." >&2
  exit 1
fi

echo "Building application and load-generator images..."
docker build -t "$SERVER_IMAGE" app/server
docker build -t "$GATEWAY_IMAGE" app/gateway
docker build --provenance=false -t "$AUTOSCALER_IMAGE" infra/autoscaler
docker build -t "$LOADGEN_IMAGE" tools/loadgen
docker build --provenance=false -t prom/prometheus:v3.13.1 infra/autoscaler/prometheus
docker build --provenance=false -t "$VALKEY_AUTOSCALER_IMAGE" infra/valkey/autoscaler

echo "Loading image into kind..."
kind load docker-image "$SERVER_IMAGE" "$GATEWAY_IMAGE" "$AUTOSCALER_IMAGE" prom/prometheus:v3.13.1 "$VALKEY_AUTOSCALER_IMAGE" --name "$CLUSTER"

echo "Installing autoscaling dependencies..."
bash "$ROOT/infra/autoscaler/install-kind.sh"

echo "Installing Valkey Operator v0.4.0..."
helm repo add valkey https://valkey.io/valkey-helm --force-update
helm upgrade --install valkey-operator valkey/valkey-operator --version 0.4.0 \
  --namespace valkey-operator-system --create-namespace \
  --values infra/valkey/operator-values.yaml --wait --timeout 3m

echo "Applying Kubernetes resources..."
kubectl apply -f deploy/base/namespace.yaml
kubectl apply -k deploy/overlays/kind
kubectl wait --for=condition=Ready valkeycluster/tcp-lab -n tcp-lab --timeout=300s
kubectl rollout restart deployment/tcp-server -n tcp-lab
kubectl rollout restart deployment/gateway -n tcp-lab
kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s
kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
kubectl rollout status deployment/gateway-autoscaler -n tcp-lab --timeout=180s
kubectl rollout status deployment/tcp-server-autoscaler -n tcp-lab --timeout=180s
kubectl rollout status deployment/valkey-shard-autoscaler -n tcp-lab --timeout=180s

echo
echo "Ready."
echo "TCP endpoint: 127.0.0.1:9000"
echo "Gateway stats: http://127.0.0.1:8404/"
echo "Map client: python3 tools/client.py"
