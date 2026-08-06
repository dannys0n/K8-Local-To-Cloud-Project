#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
METRICS_SERVER="https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.1/components.yaml"
OPERATOR="https://github.com/jthomperoo/custom-pod-autoscaler-operator/releases/download/v1.4.2/cluster.yaml"

kubectl apply -f "$METRICS_SERVER"
kubectl patch deployment metrics-server -n kube-system --type strategic --patch-file "$ROOT/infra/autoscaler/metrics-server-kind-patch.yaml"
kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s

kubectl apply --server-side --force-conflicts -f "$OPERATOR"
kubectl wait --for=condition=Established customresourcedefinition/custompodautoscalers.custompodautoscaler.com --timeout=180s
kubectl rollout status deployment/custom-pod-autoscaler-operator -n default --timeout=180s
