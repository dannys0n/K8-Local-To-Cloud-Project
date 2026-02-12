#!/usr/bin/env bash
set -euo pipefail
echo "Pinging Redis at localhost:6379..."
redis-cli -h localhost -p 6379 ping || true
