SHELL := /usr/bin/env bash

.PHONY: \
	server-setup server-teardown server-up server-down \
	worker-setup worker-teardown worker-up worker-down

server-setup:
	./scripts/server-setup.sh

server-up:
	./scripts/server-up.sh

server-down:
	./scripts/server-down.sh

server-teardown:
	./scripts/server-teardown.sh

worker-setup:
	./scripts/worker-setup.sh

worker-up:
	./scripts/worker-up.sh

worker-down:
	./scripts/worker-down.sh

worker-teardown:
	./scripts/worker-teardown.sh



#On your Linux laptop (server):

#cd /home/danny/Desktop/DES499Practice/k3s
#make server-setup
#sudo cat /var/lib/rancher/k3s/server/node-token
#Copy that token value somewhere temporarily.
#On your Raspberry Pi (worker):
#
#cd /home/danny/Desktop/DES499Practice/k3s
#make worker-setup
#make worker-up
#When prompted, enter:
#server IP/hostname (example: 192.168.1.10)
#token from the laptop
#Back on the laptop, verify both nodes joined:
#
#sudo k3s kubectl get nodes -o wide
#Useful later commands:
#
#Stop server: make server-down
#Start server again: make server-up
#Remove worker k3s: make worker-teardown
#Remove server k3s: make server-teardown