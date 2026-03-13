#!/usr/bin/env bash

tier_root() {
  printf '%s\n' "${TIER_ROOT:?TIER_ROOT must be set before sourcing common/lib.sh}"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

log() {
  echo "==> $*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it first."
}

load_env() {
  local env_file
  env_file="${ENV_FILE:-$(tier_root)/.env}"
  if [[ -f "$env_file" ]]; then
    # shellcheck disable=SC1090
    source "$env_file"
  fi
}

ensure_parent_dir() {
  mkdir -p "$(dirname "$1")"
}

resolve_home_path() {
  local raw="$1"
  printf '%s\n' "${raw/#\~/$HOME}"
}

first_token() {
  local value="${1:-}"
  set -- $value
  printf '%s\n' "${1:-}"
}

default_kubeconfig_path() {
  printf '%s\n' "$HOME/.kube/config"
}

activate_kubeconfig_if_present() {
  local kubeconfig_path="$1"
  if [[ -f "$kubeconfig_path" ]]; then
    export KUBECONFIG="$kubeconfig_path"
  fi
}
