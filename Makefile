up:
	./infra/scripts/bootstrap.sh

down:
	./infra/scripts/teardown.sh

status:
	kubectl get pods -n monitoring