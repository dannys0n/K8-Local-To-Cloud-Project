#!/usr/bin/env bash
set -euo pipefail

echo "Installing Prometheus + Grafana (kube-prometheus-stack)..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --values src/monitoring/values.yaml

kubectl rollout status deployment/monitoring-grafana -n monitoring
