SHELL := /usr/bin/env bash

ENV_FILE ?= .env
ENV_EXPORTS := SERVER_IP SERVER_SSH_USER PI_WORKER_IP WORKER_SSH_USER SSH_KEY K3S_API_ENDPOINT

-include $(ENV_FILE)
export $(ENV_EXPORTS)

.PHONY: bootstrap platform nodes linux-pi

################################
# these are already called
bootstrap:
	./scripts/bootstrap.sh

platform:
	./scripts/platform.sh
################################

nodes:
	KUBECONFIG="$(PWD)/config/kubeconfig" kubectl get nodes -o wide

linux-pi:
	@: "$${SERVER_IP:?Set SERVER_IP in .env or pass SERVER_IP=...}"
	@: "$${SERVER_SSH_USER:?Set SERVER_SSH_USER in .env or pass SERVER_SSH_USER=...}"
	@: "$${PI_WORKER_IP:?Set PI_WORKER_IP in .env or pass PI_WORKER_IP=...}"
	./scripts/bootstrap.sh \
	  --server-ip "$$SERVER_IP" \
	  --server-user "$$SERVER_SSH_USER" \
	  --worker-ip "$$PI_WORKER_IP" \
	  --worker-user "$${WORKER_SSH_USER:-pi}" \
	  --ssh-key "$${SSH_KEY:-$$HOME/.ssh/id_ed25519}" \
	  --api-endpoint "$${K3S_API_ENDPOINT:-$$SERVER_IP}"
