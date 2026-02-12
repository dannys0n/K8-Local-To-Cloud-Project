up: cluster monitors managers databases port-forward

cluster:
	./infra/scripts/cluster.sh

monitors:
	./infra/scripts/monitors.sh

managers:
	./infra/scripts/managers.sh

databases:
	kubectl apply -f infra/databases/redis.yaml
	kubectl rollout status deployment/redis -n databases

port-forward:
	./infra/scripts/port-forward.sh

down:
	./infra/scripts/teardown.sh

status:
	kubectl get pods -n monitoring

ping-redis:
	./infra/scripts/ping-redis.sh