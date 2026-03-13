#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

: "${K3S_CONTEXT_NAME:=template-tier3}"

env TIER_ROOT="$TIER_ROOT" "${TIER_ROOT}/../common/bootstrap_k3sup.sh"
"${SCRIPT_DIR}/deploy.sh"
