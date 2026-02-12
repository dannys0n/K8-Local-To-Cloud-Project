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

ping-redis:
	./src/scripts/ping-redis.sh

ping-postgres:
	./src/scripts/ping-postgres.sh