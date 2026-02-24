#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-mycluster}"

helm -n monitoring uninstall kps 2>/dev/null || true
helm -n kube-system uninstall headlamp 2>/dev/null || true

kubectl delete namespace monitoring --ignore-not-found

k3d cluster delete "${CLUSTER_NAME}" || true
