#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME=dev

command -v kind >/dev/null
command -v kubectl >/dev/null
command -v helm >/dev/null

echo "Creating kind cluster..."
kind create cluster \
  --name "${CLUSTER_NAME}" \
  --config src/kind/cluster.yaml || true

echo "Installing metrics-server for HPA..."
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]' || true
kubectl rollout status deployment/metrics-server -n kube-system --timeout=60s || true
