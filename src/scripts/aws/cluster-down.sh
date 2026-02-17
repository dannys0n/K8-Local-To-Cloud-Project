#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-des499-eks-dev}"

echo "Deleting EKS cluster '${AWS_CLUSTER_NAME}' in ${AWS_REGION}..."

if ! aws eks describe-cluster --region "${AWS_REGION}" --name "${AWS_CLUSTER_NAME}" >/dev/null 2>&1; then
  echo "Cluster '${AWS_CLUSTER_NAME}' not found. Nothing to delete."
  exit 0
fi

eksctl delete cluster \
  --name "${AWS_CLUSTER_NAME}" \
  --region "${AWS_REGION}" \
  --wait

echo "Cluster deletion complete."
