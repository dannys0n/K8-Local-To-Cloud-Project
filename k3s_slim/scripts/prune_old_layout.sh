#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# This repo previously shipped with a larger shell-script layout under scripts/cluster and scripts/orchestrate.
# This helper deletes the known legacy directories so an unzip "overwrite" doesn't leave old files behind.

LEGACY_PATHS=(
  "$ROOT_DIR/scripts/cluster"
  "$ROOT_DIR/scripts/orchestrate"
  "$ROOT_DIR/scripts/lib"
  "$ROOT_DIR/docs"
)

echo "Pruning legacy directories (if present) ..."
for p in "${LEGACY_PATHS[@]}"; do
  if [[ -e "$p" ]]; then
    echo " - removing: $p"
    rm -rf "$p"
  fi
done

echo "Done."
