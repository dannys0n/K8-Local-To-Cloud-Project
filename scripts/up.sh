#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="tcp-lab"
IMAGE="simple-tcp-server:dev"

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

echo "Building $IMAGE..."
docker build -t "$IMAGE" app/server

echo "Loading image into kind..."
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo "Applying Kubernetes resources..."
kubectl apply -k deploy/overlays/kind
kubectl rollout restart statefulset/tcp-server -n tcp-lab
kubectl rollout status statefulset/tcp-server -n tcp-lab --timeout=180s
kubectl rollout status deployment/haproxy -n tcp-lab --timeout=180s

echo
echo "Ready."
echo "TCP endpoint: 127.0.0.1:9000"
echo "HAProxy stats: http://127.0.0.1:8404/stats"
echo "Run: python3 tools/client.py"
