SHELL := /usr/bin/env bash

ENV_FILE ?= .env
-include $(ENV_FILE)

.PHONY: help k3d k3sup

help:
	@printf '%s\n' \
		'k3d   Create a k3d cluster with the configured server and agent counts' \
		'k3sup Install k3s and join every worker listed in WORKER_IPS'

k3d:
	./scripts/run_k3d.sh

k3sup:
	./scripts/run_k3sup.sh
