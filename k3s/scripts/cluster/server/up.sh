#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/../../lib/common.sh"

echo "== Server up =="

sudo_if_needed systemctl start k3s

echo "Waiting for k3s API..."
if ! wait_for_k3s 60; then
  echo "k3s not ready. Check: sudo journalctl -u k3s -n 200 --no-pager"
  exit 1
fi

sudo_if_needed k3s kubectl get nodes -o wide
