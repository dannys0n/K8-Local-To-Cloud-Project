SHELL := /usr/bin/env bash

ENV_FILE ?= .env
-include $(ENV_FILE)

.PHONY: help k3d k3sup deploy render

help:
	@printf '%s\n' \
		'k3d    Create the configured k3d cluster and deploy services/workloads' \
		'k3sup  Create the configured k3s cluster and deploy services/workloads' \
		'deploy Install cluster services and deploy the base-stack chart' \
		'render Render the base-stack chart locally'

k3d:
	./scripts/run_k3d.sh

k3sup:
	./scripts/run_k3sup.sh

deploy:
	./scripts/deploy.sh

render:
	@if [[ -n "$${BASE_STACK_VALUES:-}" ]]; then \
		helm template "$${BASE_STACK_RELEASE:-base-stack}" ./charts/base-stack \
			--namespace "$${BASE_STACK_NAMESPACE:-base-stack}" \
			-f "$${BASE_STACK_VALUES}"; \
	else \
		helm template "$${BASE_STACK_RELEASE:-base-stack}" ./charts/base-stack \
			--namespace "$${BASE_STACK_NAMESPACE:-base-stack}"; \
	fi
