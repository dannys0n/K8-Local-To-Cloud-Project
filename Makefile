KIND_CLUSTER ?= dev
CLIENTS ?= 50

# To get running:
# make up
# make game-testbed
# make proxy-port-forward-local
# game-load-local CLIENTS=100

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

# --- Database cleanups

# run if problems with database ports
free-local-db-ports:
	bash ./src/scripts/free-local-db-ports.sh

clear-databases:
	bash ./src/scripts/clear-databases.sh

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
