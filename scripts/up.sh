#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="tcp-lab"
SERVER_IMAGE="simple-tcp-server:dev"
GATEWAY_IMAGE="tcp-gateway:dev"

for command in docker kind kubectl; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command not found: $command" >&2; exit 1; }
done

cd "$ROOT"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "Creating kind cluster '$CLUSTER'..."
  kind create cluster --config kind/cluster.yaml
else
  echo "kind cluster '$CLUSTER' already exists."
fi

DATABASE_NODE="${CLUSTER}-worker2"
echo "Labeling and tainting kind database node '$DATABASE_NODE'..."
kubectl label node "$DATABASE_NODE" tcp-lab.io/database=true --overwrite
kubectl taint node "$DATABASE_NODE" tcp-lab.io/database=true:NoSchedule --overwrite

echo "Building $SERVER_IMAGE and $GATEWAY_IMAGE..."
docker build -t "$SERVER_IMAGE" app/server
docker build -t "$GATEWAY_IMAGE" app/gateway

echo "Loading image into kind..."
kind load docker-image "$SERVER_IMAGE" "$GATEWAY_IMAGE" --name "$CLUSTER"

echo "Applying Kubernetes resources..."
kubectl apply -k deploy/overlays/kind
kubectl rollout restart deployment/tcp-server -n tcp-lab
kubectl rollout restart deployment/gateway -n tcp-lab
kubectl rollout status deployment/tcp-server -n tcp-lab --timeout=180s
kubectl rollout status deployment/gateway -n tcp-lab --timeout=180s

echo
echo "Ready."
echo "TCP endpoint: 127.0.0.1:9000"
echo "Gateway stats: http://127.0.0.1:8404/"
echo "Run: python3 tools/client.py"
