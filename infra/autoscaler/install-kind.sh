#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPERATOR="https://github.com/jthomperoo/custom-pod-autoscaler-operator/releases/download/v1.4.2/cluster.yaml"

kubectl apply -f "$ROOT/deploy/base/namespace.yaml"
kubectl apply -k "$ROOT/infra/autoscaler/prometheus"
kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s

kubectl apply --server-side --force-conflicts -f "$OPERATOR"
kubectl wait --for=condition=Established customresourcedefinition/custompodautoscalers.custompodautoscaler.com --timeout=180s
kubectl rollout status deployment/custom-pod-autoscaler-operator -n default --timeout=180s
