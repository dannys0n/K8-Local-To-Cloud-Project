#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "Installing dependencies..."
npm install --legacy-peer-deps

echo ""
echo "Syncing database schema..."
npx prisma db push

echo ""
echo "Starting dev server..."
npm run dev
