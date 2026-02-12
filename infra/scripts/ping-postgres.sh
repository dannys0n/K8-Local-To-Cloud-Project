#!/usr/bin/env bash
set -euo pipefail

echo "Pinging Postgres at localhost:5432..."
pg_isready -h localhost -p 5432 -U postgres -d app || true

