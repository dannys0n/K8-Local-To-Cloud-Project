#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-dev}"

BACKEND_REPO="${BACKEND_REPO:-game-backend}"
PROXY_REPO="${PROXY_REPO:-game-proxy}"
SERVER_REPO="${SERVER_REPO:-game-server}"

REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

build_tag_push() {
  local local_name="$1"
  local context_dir="$2"
  local repo="$3"
  local remote="${REGISTRY}/${repo}:${AWS_IMAGE_TAG}"

  echo "Building ${local_name}:${AWS_IMAGE_TAG} from ${context_dir}..."
  docker build -t "${local_name}:${AWS_IMAGE_TAG}" "${context_dir}"

  echo "Tagging ${remote}..."
  docker tag "${local_name}:${AWS_IMAGE_TAG}" "${remote}"

  echo "Pushing ${remote}..."
  docker push "${remote}"
}

build_tag_push "game-backend" "src/app/backend" "${BACKEND_REPO}"
build_tag_push "game-proxy" "src/app/proxy" "${PROXY_REPO}"
build_tag_push "game-server" "src/app/game-server" "${SERVER_REPO}"

echo "Build/push complete with tag ${AWS_IMAGE_TAG}."
