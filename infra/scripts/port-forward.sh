#!/usr/bin/env bash
set -euo pipefail

PID_DIR=.pids
mkdir -p "${PID_DIR}"

start_pf () {
  local name=$1
  local namespace=$2
  local svc=$3
  local local_port=$4
  local remote_port=$5

  if [[ -f "${PID_DIR}/${name}.pid" ]] && kill -0 "$(cat "${PID_DIR}/${name}.pid")" 2>/dev/null; then
    return
  fi

  kubectl port-forward -n "${namespace}" "svc/${svc}" \
    "${local_port}:${remote_port}" \
    > /dev/null 2>&1 &

  echo $! > "${PID_DIR}/${name}.pid"
}

# Monitoring
start_pf grafana     monitoring monitoring-grafana                     3000 80
start_pf prometheus  monitoring monitoring-kube-prometheus-prometheus  9090 9090

# Managing (Portainer)
start_pf portainer   portainer  portainer                             9000 9000

echo ""
echo "kubectl get secret --namespace monitoring -l app.kubernetes.io/component=admin-secret -o jsonpath="{.items[0].data.admin-password}" | base64 --decode ; echo"
echo "READY:"
echo "  Grafana    http://localhost:3000"
echo "  Prometheus http://localhost:9090"
echo "  Portainer  http://localhost:9000 (create admin on first visit)"