#!/usr/bin/env bash
set -euo pipefail

./infra/scripts/stop-port-forward.sh || true
kind delete cluster --name dev
