#!/usr/bin/env bash
set -euo pipefail

echo "Clearing Redis..."
kubectl exec deployment/redis -n databases -- redis-cli FLUSHALL || echo "Redis flush failed"

echo "Clearing Postgres..."
if kubectl exec deployment/postgres -n databases -- psql -U postgres -d app -tAc "SELECT to_regclass('public.matches') IS NOT NULL;" | grep -q "t"; then
  kubectl exec deployment/postgres -n databases -- psql -U postgres -d app -c "TRUNCATE TABLE matches CASCADE;" || echo "Postgres truncate failed"
else
  echo "matches table does not exist yet, skipping truncate"
fi

kubectl exec deployment/postgres -n databases -- psql -U postgres -d app -c "DROP TABLE IF EXISTS match_events, game_server_stats;" || echo "Postgres telemetry table cleanup failed"

echo "Cleaning up orphaned game server pods and services..."
kubectl delete deployment -l app=game-server --ignore-not-found || true
kubectl delete service -l app=game-server --ignore-not-found || true

echo "Databases cleared!"
