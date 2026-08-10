#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

kubectl apply -f "$ROOT/deploy/base/namespace.yaml"
kubectl apply -k "$ROOT/infra/autoscaler/metrics-server-kind"
kubectl apply -k "$ROOT/infra/autoscaler/prometheus"
kubectl rollout restart deployment/prometheus -n tcp-lab
kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s
kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
