#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME=dev

command -v kind >/dev/null
command -v kubectl >/dev/null
command -v helm >/dev/null

echo "Creating kind cluster..."
kind create cluster \
  --name "${CLUSTER_NAME}" \
  --config infra/kind/cluster.yaml || true

echo "Installing Prometheus + Grafana (kube-prometheus-stack)..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --values infra/monitoring/values.yaml

kubectl rollout status deployment/monitoring-grafana -n monitoring

echo "Installing Headlamp (managing UI)..."
kubectl apply -f infra/managing/rbac.yaml
kubectl apply -f infra/managing/headlamp.yaml
kubectl rollout status deployment/headlamp -n headlamp

./infra/scripts/port-forward.sh
