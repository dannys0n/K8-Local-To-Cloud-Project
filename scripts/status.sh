#!/usr/bin/env bash
set -euo pipefail
kubectl get nodes -o wide
kubectl get pods,svc,pvc,pdb -n tcp-lab -o wide
