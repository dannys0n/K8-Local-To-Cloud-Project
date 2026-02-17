#!/usr/bin/env bash
set -euo pipefail

echo "Cleaning dynamic game server resources..."
kubectl delete deployment,svc -l app=game-server --ignore-not-found

echo "Cleaning core app resources..."
kubectl delete deployment game-backend --ignore-not-found
kubectl delete deployment game-proxy --ignore-not-found
kubectl delete service game-backend --ignore-not-found
kubectl delete service game-proxy --ignore-not-found
kubectl delete hpa game-backend-hpa --ignore-not-found
kubectl delete hpa game-proxy-hpa --ignore-not-found

echo "Cleaning database resources..."
kubectl delete deployment postgres -n databases --ignore-not-found
kubectl delete deployment redis -n databases --ignore-not-found
kubectl delete service postgres -n databases --ignore-not-found
kubectl delete service redis -n databases --ignore-not-found
kubectl delete namespace databases --ignore-not-found

echo "App resource cleanup complete. Cluster remains running."
