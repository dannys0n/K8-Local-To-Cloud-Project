#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

: "${CLUSTER_NAME:=template-tier3}"
: "${K3D_SERVER_COUNT:=1}"
: "${K3D_AGENT_COUNT:=2}"

env TIER_ROOT="$TIER_ROOT" "${TIER_ROOT}/../common/bootstrap_k3d.sh"
"${SCRIPT_DIR}/deploy.sh"
