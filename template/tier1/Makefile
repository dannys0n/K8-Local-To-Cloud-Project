SHELL := /usr/bin/env bash

ENV_FILE ?= .env
-include $(ENV_FILE)

.PHONY: help k3d k3sup

help:
	@printf '%s\n' \
		'k3d   Create a minimal single-node k3d cluster' \
		'k3sup Install a minimal single-server k3s cluster with k3sup'

k3d:
	./scripts/run_k3d.sh

k3sup:
	./scripts/run_k3sup.sh
