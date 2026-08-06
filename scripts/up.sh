#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="tcp-lab"
SERVER_IMAGE="simple-tcp-server:dev"
GATEWAY_IMAGE="tcp-gateway:dev"
AUTOSCALER_IMAGE="tcp-server-autoscaler:dev"

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

DATABASE_NODE="${CLUSTER}-worker"
mapfile -t EXISTING_DATABASE_NODES < <(kubectl get nodes -l tcp-lab.io/database=true -o name)
for node in "${EXISTING_DATABASE_NODES[@]}"; do
  if [[ "$node" != "node/$DATABASE_NODE" ]]; then
    echo "Database node placement changed. Recreate the kind cluster before running up.sh so the node-local PostgreSQL volume is not stranded." >&2
    exit 1
  fi
done
echo "Labeling and tainting kind database node '$DATABASE_NODE'..."
kubectl label node "$DATABASE_NODE" tcp-lab.io/database=true --overwrite
kubectl taint node "$DATABASE_NODE" tcp-lab.io/database=true:NoSchedule --overwrite

echo "Building $SERVER_IMAGE, $GATEWAY_IMAGE, and $AUTOSCALER_IMAGE..."
docker build -t "$SERVER_IMAGE" app/server
docker build -t "$GATEWAY_IMAGE" app/gateway
docker build -t "$AUTOSCALER_IMAGE" infra/autoscaler

echo "Loading image into kind..."
kind load docker-image "$SERVER_IMAGE" "$GATEWAY_IMAGE" "$AUTOSCALER_IMAGE" --name "$CLUSTER"

echo "Installing autoscaling dependencies..."
bash "$ROOT/infra/autoscaler/install-kind.sh"

echo "Applying Kubernetes resources..."
kubectl apply -f deploy/base/namespace.yaml
kubectl delete job/tcp-server-schema -n tcp-lab --ignore-not-found
kubectl apply -k deploy/overlays/kind
kubectl wait --for=condition=Complete job/tcp-server-schema -n tcp-lab --timeout=180s
kubectl rollout restart deployment/tcp-server -n tcp-lab
kubectl rollout restart deployment/gateway -n tcp-lab
kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s
kubectl wait --for=condition=Ready pod -n tcp-lab -l app=tcp-server-autoscaler --timeout=180s

echo
echo "Ready."
echo "TCP endpoint: 127.0.0.1:9000"
echo "Gateway stats: http://127.0.0.1:8404/"
echo "Map client: python3 tools/client.py"
