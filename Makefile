up: cluster monitors managers databases port-forward

cluster:
	./infra/scripts/cluster.sh

monitors:
	./infra/scripts/monitors.sh

managers:
	./infra/scripts/managers.sh

databases: redis postgres

databases-namespace:
	kubectl apply -f infra/databases/namespace.yaml

redis: databases-namespace
	kubectl apply -f infra/databases/redis.yaml
	kubectl rollout status deployment/redis -n databases

postgres: databases-namespace
	kubectl apply -f infra/databases/postgres.yaml
	kubectl rollout status deployment/postgres -n databases

port-forward:
	./infra/scripts/port-forward.sh

down:
	./infra/scripts/teardown.sh

status:
	kubectl get pods -n monitoring

ping-redis:
	./infra/scripts/ping-redis.sh

ping-postgres:
	./infra/scripts/ping-postgres.sh