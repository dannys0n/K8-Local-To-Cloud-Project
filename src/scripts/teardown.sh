#!/usr/bin/env bash
set -euo pipefail

./src/scripts/stop-port-forward.sh || true
kind delete cluster --name dev
