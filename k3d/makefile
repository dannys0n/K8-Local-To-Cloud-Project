# Minimal Makefile
# Usage:
#   make up
#   make down

CLUSTER_NAME ?= mycluster

.PHONY: up down

up:
	CLUSTER_NAME="$(CLUSTER_NAME)" ./scripts/install.sh

down:
	CLUSTER_NAME="$(CLUSTER_NAME)" ./scripts/uninstall.sh

access:
	CLUSTER_NAME="$(CLUSTER_NAME)" ./scripts/access.sh