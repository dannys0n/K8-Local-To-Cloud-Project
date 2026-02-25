\
#!/usr/bin/env bash
set -euo pipefail

require_sudo() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "sudo is required but not installed."
      exit 1
    fi
  fi
}

sudo_if_needed() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

os_id() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    echo "${ID:-unknown}"
  else
    echo "unknown"
  fi
}

is_debian_like() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ "${ID_LIKE:-}" == *"debian"* ]] || [[ "${ID:-}" == "debian" ]] || [[ "${ID:-}" == "ubuntu" ]] || [[ "${ID:-}" == "raspbian" ]]
  else
    return 1
  fi
}

apt_install() {
  require_sudo
  if ! is_debian_like; then
    echo "This script currently supports Debian/Ubuntu/Raspberry Pi OS."
    exit 1
  fi
  sudo_if_needed apt-get update
  sudo_if_needed apt-get install -y "$@"
}

wait_for_k3s() {
  local tries="${1:-60}"
  for _ in $(seq 1 "$tries"); do
    if command -v kubectl >/dev/null 2>&1 && kubectl get nodes >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

write_file_root() {
  local path="$1"
  local content="$2"
  require_sudo
  sudo_if_needed mkdir -p "$(dirname "$path")"
  printf "%s" "$content" | sudo_if_needed tee "$path" >/dev/null
}
