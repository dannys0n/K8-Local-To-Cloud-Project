#!/usr/bin/env bash
set -euo pipefail

PID_DIR=.pids
[[ -d "${PID_DIR}" ]] || exit 0

for pidfile in "${PID_DIR}"/*.pid; do
  [[ -f "${pidfile}" ]] || continue
  pid=$(cat "${pidfile}")
  kill "${pid}" 2>/dev/null || true
done

rm -rf "${PID_DIR}"
