#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

kubectl apply -f "$ROOT/deploy/base/namespace.yaml"
kubectl apply -k "$ROOT/infra/autoscaler/prometheus"
kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
