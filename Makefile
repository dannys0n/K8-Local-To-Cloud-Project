ifneq (,$(wildcard .env))
include .env
export
endif

KIND_CLUSTER ?= dev
CLIENTS ?= 50
AWS_REGION ?= us-east-1
AWS_CLUSTER_NAME ?= des499-eks-dev
AWS_K8S_VERSION ?= 1.31
AWS_NODE_TYPE ?= t3.medium
AWS_NODE_COUNT ?= 2
AWS_ACCOUNT_ID ?=
AWS_IMAGE_TAG ?= dev
BACKEND_REPO ?= game-backend
PROXY_REPO ?= game-proxy
SERVER_REPO ?= game-server
PROXY_SERVICE_PORT ?= 8080

# To get running:
# make up
# make game-testbed
# make proxy-port-forward-local
# game-load-local CLIENTS=100

up: cluster monitors managers databases port-forward

cluster:
	./src/scripts/cluster.sh

monitors:
	./src/scripts/monitors.sh

managers:
	./src/scripts/managers.sh

databases: redis postgres

databases-namespace:
	kubectl apply -f src/databases/namespace.yaml

redis: databases-namespace
	kubectl apply -f src/databases/redis.yaml
	kubectl rollout status deployment/redis -n databases

postgres: databases-namespace
	kubectl apply -f src/databases/postgres.yaml
	kubectl rollout status deployment/postgres -n databases

port-forward:
	./src/scripts/port-forward.sh


down:
	./src/scripts/teardown.sh

status:
	kubectl get pods -n monitoring

# --- Database cleanups

# run if problems with database ports
free-local-db-ports:
	bash ./src/scripts/free-local-db-ports.sh

clear-databases:
	bash ./src/scripts/clear-databases.sh

# --- Game backend testbed:
# 1
game-testbed: game-build game-load-images game-backend game-proxy
# 2
proxy-port-forward-local:
	kubectl port-forward svc/game-proxy 8080:8080
# 3
game-load-local:
	cd src/app/load && python3 main.py $(CLIENTS)

game-build:
	docker build -t game-backend:local src/app/backend
	docker build -t game-proxy:local  src/app/proxy
	docker build -t game-server:local  src/app/game-server

game-load-images:
	kind load docker-image game-backend:local --name $(KIND_CLUSTER)
	kind load docker-image game-proxy:local  --name $(KIND_CLUSTER)
	kind load docker-image game-server:local --name $(KIND_CLUSTER)

game-backend:
	kubectl apply -f src/k8s/base/backend-rbac.yaml
	kubectl apply -f src/k8s/base/backend.yaml
	kubectl apply -f src/k8s/base/backend-hpa.yaml

game-proxy:
	kubectl apply -f src/k8s/base/proxy.yaml
	kubectl apply -f src/k8s/base/proxy-hpa.yaml

ping-redis:
	./src/scripts/ping-redis.sh

ping-postgres:
	./src/scripts/ping-postgres.sh

# --- AWS automation (EKS runbook)
# fresh start (cluster down):
#   make aws-all
#
# Daily iteration (cluster already up):
#   make aws-iterate
#
# Cleanup:
#   make aws-clean-app   # remove app resources, keep EKS cluster
#   make aws-down        # delete EKS cluster

# Convenience targets
aws-iterate: aws-build-push aws-deploy aws-smoke-test

aws-all: aws-up aws-build-push aws-deploy aws-smoke-test

# 1) Preflight + cluster lifecycle
aws-preflight:
	AWS_REGION=$(AWS_REGION) bash ./src/scripts/aws/preflight.sh

aws-up: aws-preflight
	AWS_REGION=$(AWS_REGION) \
	AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) \
	AWS_K8S_VERSION=$(AWS_K8S_VERSION) \
	AWS_NODE_TYPE=$(AWS_NODE_TYPE) \
	AWS_NODE_COUNT=$(AWS_NODE_COUNT) \
	bash ./src/scripts/aws/cluster-up.sh

aws-down: aws-preflight
	AWS_REGION=$(AWS_REGION) \
	AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) \
	bash ./src/scripts/aws/cluster-down.sh

aws-status:
	AWS_REGION=$(AWS_REGION) \
	AWS_CLUSTER_NAME=$(AWS_CLUSTER_NAME) \
	aws eks describe-cluster --region "$$AWS_REGION" --name "$$AWS_CLUSTER_NAME" --query 'cluster.status' --output text

# 2) Image registry + image publish
aws-ecr: aws-preflight
	AWS_REGION=$(AWS_REGION) \
	AWS_ACCOUNT_ID=$(AWS_ACCOUNT_ID) \
	BACKEND_REPO=$(BACKEND_REPO) \
	PROXY_REPO=$(PROXY_REPO) \
	SERVER_REPO=$(SERVER_REPO) \
	bash ./src/scripts/aws/ecr.sh

aws-build-push: aws-ecr
	AWS_REGION=$(AWS_REGION) \
	AWS_ACCOUNT_ID=$(AWS_ACCOUNT_ID) \
	AWS_IMAGE_TAG=$(AWS_IMAGE_TAG) \
	BACKEND_REPO=$(BACKEND_REPO) \
	PROXY_REPO=$(PROXY_REPO) \
	SERVER_REPO=$(SERVER_REPO) \
	bash ./src/scripts/aws/build-push.sh

# 3) Deploy + verify
aws-deploy:
	AWS_REGION=$(AWS_REGION) \
	AWS_ACCOUNT_ID=$(AWS_ACCOUNT_ID) \
	AWS_IMAGE_TAG=$(AWS_IMAGE_TAG) \
	BACKEND_REPO=$(BACKEND_REPO) \
	PROXY_REPO=$(PROXY_REPO) \
	SERVER_REPO=$(SERVER_REPO) \
	PROXY_SERVICE_PORT=$(PROXY_SERVICE_PORT) \
	bash ./src/scripts/aws/deploy.sh

aws-clean-app:
	bash ./src/scripts/aws/clean-app.sh

aws-smoke-test:
	PROXY_SERVICE_PORT=$(PROXY_SERVICE_PORT) \
	bash ./src/scripts/aws/smoke-test.sh

