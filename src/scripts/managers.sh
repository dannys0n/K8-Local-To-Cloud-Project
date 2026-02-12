#!/usr/bin/env bash
set -euo pipefail

echo "Installing Portainer (managing UI)..."
helm repo add portainer https://portainer.github.io/k8s/ >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install portainer portainer/portainer \
  --namespace portainer \
  --create-namespace \
  --values src/managing/portainer-values.yaml
kubectl rollout status deployment/portainer -n portainer
