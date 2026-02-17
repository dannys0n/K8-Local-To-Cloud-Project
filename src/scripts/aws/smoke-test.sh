#!/usr/bin/env bash
set -euo pipefail

PROXY_SERVICE_PORT="${PROXY_SERVICE_PORT:-8080}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-300}"

echo "Waiting for game-proxy LoadBalancer hostname..."
deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))
LB_HOST=""
while [[ ${SECONDS} -lt ${deadline} ]]; do
  LB_HOST="$(kubectl get svc game-proxy -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  if [[ -n "${LB_HOST}" ]]; then
    break
  fi
  sleep 5
done

if [[ -z "${LB_HOST}" ]]; then
  echo "Timed out waiting for game-proxy LoadBalancer hostname." >&2
  exit 1
fi

BASE_URL="http://${LB_HOST}:${PROXY_SERVICE_PORT}"
echo "Using base URL: ${BASE_URL}"

echo "Health check..."
curl -fsS --max-time 20 "${BASE_URL}/health"
echo ""

P1="smoke-$(date +%s)-a"
P2="smoke-$(date +%s)-b"

echo "Join #1 (${P1})..."
curl -fsS --max-time 90 -X POST "${BASE_URL}/api/match/join" \
  -H "Content-Type: application/json" \
  -d "{\"player_id\":\"${P1}\"}"
echo ""

echo "Join #2 (${P2})..."
curl -fsS --max-time 90 -X POST "${BASE_URL}/api/match/join" \
  -H "Content-Type: application/json" \
  -d "{\"player_id\":\"${P2}\"}"
echo ""

echo "Recent game-server pods:"
kubectl get pods | grep game-server || true

echo "Smoke test complete."
