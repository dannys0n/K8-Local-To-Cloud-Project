# scripts/access.sh
#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/access.sh
# Optional:
#   CLUSTER_NAME=mycluster ./scripts/access.sh

CLUSTER_NAME="${CLUSTER_NAME:-mycluster}"

# Ensure kubectl is pointing at the cluster
kubectl config use-context "k3d-${CLUSTER_NAME}" >/dev/null

echo "Headlamp URL: http://localhost:8081"
echo "Grafana URL:  http://localhost:3000"
echo

echo "Headlamp token (paste into Headlamp login):"
if ! kubectl -n kube-system create token headlamp-admin >/dev/null 2>&1; then
  # Bootstrap demo RBAC if it's missing
  kubectl -n kube-system create serviceaccount headlamp-admin \
    --dry-run=client -o yaml | kubectl apply -f -

  kubectl delete clusterrolebinding headlamp-admin --ignore-not-found
  kubectl create clusterrolebinding headlamp-admin \
    --serviceaccount=kube-system:headlamp-admin \
    --clusterrole=cluster-admin
fi

# Print token (short-lived)
kubectl -n kube-system create token headlamp-admin
echo

# Start port-forwards in the background
kubectl -n kube-system port-forward svc/headlamp 8081:80 >/dev/null 2>&1 &
PF_HEADLAMP_PID=$!

kubectl -n monitoring port-forward svc/kps-grafana 3000:80 >/dev/null 2>&1 &
PF_GRAFANA_PID=$!

cleanup() {
  kill "$PF_HEADLAMP_PID" "$PF_GRAFANA_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "Port-forwards running (Ctrl+C to stop)."
wait