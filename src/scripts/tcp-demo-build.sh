#!/usr/bin/env bash
set -euo pipefail

# Build tcp-demo image and load into kind (for local dev).
# Run from repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

docker build -t tcp-demo:local src/tcp-demo
kind load docker-image tcp-demo:local --name dev
