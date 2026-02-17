#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

require_cmd aws
require_cmd eksctl
require_cmd kubectl
require_cmd docker

echo "Validating AWS credentials..."
caller_json="$(aws sts get-caller-identity)"
echo "$caller_json"

echo "Tool versions:"
aws --version
kubectl version --client
eksctl version

echo "Preflight checks passed for region ${AWS_REGION}."
