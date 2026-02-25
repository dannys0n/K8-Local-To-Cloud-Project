\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Server down =="

sudo_if_needed systemctl stop k3s-lab-token || true
sudo_if_needed systemctl stop k3s || true

echo "Stopped k3s services."
