#!/usr/bin/env bash
set -euo pipefail

stop_service_if_active() {
  local service_name=$1
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; cannot stop ${service_name} automatically."
    return
  fi

  if systemctl is-active --quiet "${service_name}"; then
    echo "Stopping ${service_name}..."
    if sudo -n systemctl stop "${service_name}" >/dev/null 2>&1; then
      echo "Stopped ${service_name}."
      return
    fi

    echo "sudo password may be required to stop ${service_name}."
    if sudo systemctl stop "${service_name}"; then
      echo "Stopped ${service_name}."
    else
      echo "Failed to stop ${service_name}."
    fi
  else
    echo "${service_name} is not active."
  fi
}

check_and_free_port() {
  local port=$1
  local expected_regex=$2
  local service_name=$3
  local label=$4

  local line
  line="$(ss -ltnp 2>/dev/null | awk -v p=":${port}" '$4 ~ p"$" {print; exit}')"

  if [[ -z "${line}" ]]; then
    echo "${label} port ${port} is already free."
    return
  fi

  echo "${label} port ${port} is occupied: ${line}"

  if [[ "${line}" =~ ${expected_regex} ]]; then
    stop_service_if_active "${service_name}"
  else
    echo "Port ${port} is not occupied by host ${label}; leaving it unchanged."
  fi
}

is_service_active() {
  local service_name=$1
  if ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi
  systemctl is-active --quiet "${service_name}"
}

redis_is_responding() {
  command -v redis-cli >/dev/null 2>&1 \
    && [[ "$(redis-cli -h 127.0.0.1 -p 6379 PING 2>/dev/null || true)" == "PONG" ]]
}

postgres_is_responding() {
  command -v pg_isready >/dev/null 2>&1 \
    && pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1
}

# Prefer process-name check. If process names are hidden on this host, fall back
# to checking whether local Redis/Postgres services are active and responding.
check_and_free_port 6379 "redis-server" "redis-server" "Redis"
if is_service_active "redis-server" && redis_is_responding; then
  stop_service_if_active "redis-server"
fi

check_and_free_port 5432 "(postgres|postmaster)" "postgresql" "Postgres"
if is_service_active "postgresql" && postgres_is_responding; then
  stop_service_if_active "postgresql"
fi

echo "Done. You can now run your preferred kubectl port-forward commands."
