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
