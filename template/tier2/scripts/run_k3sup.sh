#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

: "${SERVER_IPS:?Set SERVER_IPS in .env}"
: "${SERVER_SSH_USER:?Set SERVER_SSH_USER in .env}"
: "${WORKER_IPS:?Set WORKER_IPS in .env}"
: "${WORKER_SSH_USER:?Set WORKER_SSH_USER in .env}"
: "${SSH_KEY:=~/.ssh/id_ed25519}"
: "${K3SUP_USE_SUDO:=true}"
: "${K3S_CONTEXT_NAME:=template-tier2}"

exec env TIER_ROOT="$TIER_ROOT" "${TIER_ROOT}/../common/bootstrap_k3sup.sh"
