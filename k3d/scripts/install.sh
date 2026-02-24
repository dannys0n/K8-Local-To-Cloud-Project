#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-mycluster}"

# Expose HTTP/HTTPS through the k3d load balancer (useful for optional Ingress)
k3d cluster create "${CLUSTER_NAME}"   -p "8080:80@loadbalancer"   -p "8443:443@loadbalancer"   --servers 1 --agents 0

kubectl config use-context "k3d-${CLUSTER_NAME}"

# Helm repos
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/
helm repo update

# Namespaces
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

# Monitoring stack (Prometheus + Grafana)
helm upgrade --install kps prometheus-community/kube-prometheus-stack   -n monitoring   -f helm-values/kps.dev.yaml

# Headlamp (install into kube-system, as in upstream docs/examples)
helm upgrade --install headlamp headlamp/headlamp   -n kube-system   -f helm-values/headlamp.dev.yaml

# Demo RBAC for Headlamp (admin)
kubectl -n kube-system create serviceaccount headlamp-admin \
  --dry-run=client -o yaml | kubectl apply -f -

# Ensure the binding is correct (safe to recreate for a demo)
kubectl delete clusterrolebinding headlamp-admin --ignore-not-found
kubectl create clusterrolebinding headlamp-admin \
  --serviceaccount=kube-system:headlamp-admin \
  --clusterrole=cluster-admin

echo
echo "Installed."
echo "Grafana (port-forward):  kubectl -n monitoring port-forward svc/kps-grafana 3000:80"
echo "Headlamp (port-forward): kubectl -n kube-system port-forward svc/headlamp 8081:80"
echo "Optional ingress:        kubectl apply -f manifests/ingress.yaml  (then use http://*.localtest.me:8080)"
