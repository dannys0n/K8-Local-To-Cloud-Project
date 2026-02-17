KIND_CLUSTER ?= dev
CLIENTS ?= 50

up: cluster monitors managers databases port-forward

cluster:
	./src/scripts/cluster.sh

monitors:
	./src/scripts/monitors.sh

managers:
	./src/scripts/managers.sh

databases: redis postgres

databases-namespace:
	kubectl apply -f src/databases/namespace.yaml

redis: databases-namespace
	kubectl apply -f src/databases/redis.yaml
	kubectl rollout status deployment/redis -n databases

postgres: databases-namespace
	kubectl apply -f src/databases/postgres.yaml
	kubectl rollout status deployment/postgres -n databases

port-forward:
	./src/scripts/port-forward.sh

down:
	./src/scripts/teardown.sh

status:
	kubectl get pods -n monitoring

# --- Game backend testbed:
# 1
game-testbed: game-build game-load-images game-backend game-proxy
# 2
proxy-port-forward-local:
	kubectl port-forward svc/game-proxy 8080:8080
# 3
game-load-local:
	cd src/app/load && python3 main.py $(CLIENTS)

game-build:
	docker build -t game-backend:local src/app/backend
	docker build -t game-proxy:local  src/app/proxy
	docker build -t game-server:local  src/app/game-server

game-load-images:
	kind load docker-image game-backend:local --name $(KIND_CLUSTER)
	kind load docker-image game-proxy:local  --name $(KIND_CLUSTER)
	kind load docker-image game-server:local --name $(KIND_CLUSTER)

game-backend:
	kubectl apply -f src/k8s/base/backend-rbac.yaml
	kubectl apply -f src/k8s/base/backend.yaml
	kubectl apply -f src/k8s/base/backend-hpa.yaml

game-proxy:
	kubectl apply -f src/k8s/base/proxy.yaml
	kubectl apply -f src/k8s/base/proxy-hpa.yaml

ping-redis:
	./src/scripts/ping-redis.sh

ping-postgres:
	./src/scripts/ping-postgres.sh

clear-databases:
	@echo "Clearing Redis..."
	@kubectl exec deployment/redis -n databases -- redis-cli FLUSHALL || echo "Redis flush failed"
	@echo "Clearing Postgres..."
	@kubectl exec deployment/postgres -n databases -- psql -U postgres -d app -c "TRUNCATE TABLE IF EXISTS matches CASCADE;" || echo "Postgres truncate failed"
	@kubectl exec deployment/postgres -n databases -- psql -U postgres -d app -c "DROP TABLE IF EXISTS match_events, game_server_stats;" || echo "Postgres telemetry table cleanup failed"
	@echo "Cleaning up orphaned game server pods and services..."
	@kubectl delete deployment -l app=game-server --ignore-not-found || true
	@kubectl delete service -l app=game-server --ignore-not-found || true
	@echo "Databases cleared!"

