SHELL := /usr/bin/env bash

.PHONY: bootstrap platform nodes

bootstrap:
	./scripts/bootstrap.sh

platform:
	./scripts/platform.sh

nodes:
	KUBECONFIG="$(PWD)/config/kubeconfig" kubectl get nodes -o wide
