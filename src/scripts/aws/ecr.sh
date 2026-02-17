#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"

BACKEND_REPO="${BACKEND_REPO:-game-backend}"
PROXY_REPO="${PROXY_REPO:-game-proxy}"
SERVER_REPO="${SERVER_REPO:-game-server}"

ensure_repo() {
  local repo="$1"
  if aws ecr describe-repositories --region "${AWS_REGION}" --repository-names "${repo}" >/dev/null 2>&1; then
    echo "ECR repository exists: ${repo}"
  else
    echo "Creating ECR repository: ${repo}"
    aws ecr create-repository --region "${AWS_REGION}" --repository-name "${repo}" >/dev/null
  fi
}

ensure_repo "${BACKEND_REPO}"
ensure_repo "${PROXY_REPO}"
ensure_repo "${SERVER_REPO}"

echo "Logging Docker into ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "ECR ready in ${AWS_REGION} for account ${AWS_ACCOUNT_ID}."
