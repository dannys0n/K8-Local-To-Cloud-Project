#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-dev}"

BACKEND_REPO="${BACKEND_REPO:-game-backend}"
PROXY_REPO="${PROXY_REPO:-game-proxy}"
SERVER_REPO="${SERVER_REPO:-game-server}"
PROXY_SERVICE_PORT="${PROXY_SERVICE_PORT:-8080}"

REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
BACKEND_IMAGE="${REGISTRY}/${BACKEND_REPO}:${AWS_IMAGE_TAG}"
PROXY_IMAGE="${REGISTRY}/${PROXY_REPO}:${AWS_IMAGE_TAG}"
GAME_SERVER_IMAGE="${REGISTRY}/${SERVER_REPO}:${AWS_IMAGE_TAG}"

echo "Applying database resources..."
kubectl apply -f src/databases/namespace.yaml
kubectl apply -f src/databases/postgres.yaml
kubectl apply -f src/databases/redis.yaml
kubectl rollout status deployment/postgres -n databases --timeout=180s
kubectl rollout status deployment/redis -n databases --timeout=180s

echo "Applying game backend/proxy resources..."
kubectl apply -f src/k8s/base/backend-rbac.yaml
kubectl apply -f src/k8s/base/backend.yaml
kubectl apply -f src/k8s/base/proxy.yaml

echo "Setting deployment images..."
kubectl set image deployment/game-backend backend="${BACKEND_IMAGE}"
kubectl set image deployment/game-proxy proxy="${PROXY_IMAGE}"

echo "Configuring backend runtime env for dynamic game servers..."
kubectl set env deployment/game-backend \
  GAME_SERVER_IMAGE="${GAME_SERVER_IMAGE}"

echo "Ensuring proxy is externally accessible..."
kubectl patch svc game-proxy -p '{"spec":{"type":"LoadBalancer","ports":[{"port":'"${PROXY_SERVICE_PORT}"',"targetPort":8080,"protocol":"TCP","name":"http"}]}}'

echo "Waiting for app rollouts..."
kubectl rollout status deployment/game-backend --timeout=180s
kubectl rollout status deployment/game-proxy --timeout=180s

echo "Current pods:"
kubectl get pods

echo "Proxy service:"
kubectl get svc game-proxy

echo "Deploy complete."
