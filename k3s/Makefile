SHELL := /usr/bin/env bash

.PHONY: \
	server-setup server-up server-down server-teardown \
	agent-setup agent-up agent-down agent-teardown

# Server node (run on the machine hosting k3s server)
server-setup:
	./scripts/cluster/server/setup.sh

server-up:
	./scripts/cluster/server/up.sh

server-down:
	./scripts/cluster/server/down.sh

server-teardown:
	./scripts/cluster/server/teardown.sh

# Agent/worker node (run on a worker like Raspberry Pi OS)
agent-setup:
	./scripts/cluster/agent/setup.sh

agent-up:
	./scripts/cluster/agent/up.sh

agent-down:
	./scripts/cluster/agent/down.sh

agent-teardown:
	./scripts/cluster/agent/teardown.sh
