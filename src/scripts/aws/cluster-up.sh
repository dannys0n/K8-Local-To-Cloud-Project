#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-des499-eks-dev}"
AWS_K8S_VERSION="${AWS_K8S_VERSION:-1.31}"
AWS_NODE_TYPE="${AWS_NODE_TYPE:-t3.medium}"
AWS_NODE_COUNT="${AWS_NODE_COUNT:-2}"

echo "Ensuring EKS cluster '${AWS_CLUSTER_NAME}' in ${AWS_REGION}..."

if aws eks describe-cluster --region "${AWS_REGION}" --name "${AWS_CLUSTER_NAME}" >/dev/null 2>&1; then
  status="$(aws eks describe-cluster --region "${AWS_REGION}" --name "${AWS_CLUSTER_NAME}" --query 'cluster.status' --output text)"
  echo "Cluster already exists with status: ${status}"
else
  eksctl create cluster \
    --name "${AWS_CLUSTER_NAME}" \
    --region "${AWS_REGION}" \
    --version "${AWS_K8S_VERSION}" \
    --nodes "${AWS_NODE_COUNT}" \
    --node-type "${AWS_NODE_TYPE}" \
    --managed
fi

echo "Waiting for cluster to become ACTIVE..."
aws eks wait cluster-active --region "${AWS_REGION}" --name "${AWS_CLUSTER_NAME}"

echo "Updating kubeconfig..."
aws eks update-kubeconfig --region "${AWS_REGION}" --name "${AWS_CLUSTER_NAME}"

echo "Current kubectl context:"
kubectl config current-context

echo "Cluster nodes:"
kubectl get nodes
