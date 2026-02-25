#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/../../lib/common.sh"

find_pi_cmdline() {
  if [[ -f /boot/firmware/cmdline.txt ]]; then
    echo "/boot/firmware/cmdline.txt"
    return 0
  fi
  if [[ -f /boot/cmdline.txt ]]; then
    echo "/boot/cmdline.txt"
    return 0
  fi
  return 1
}

cmdline_has_arg() {
  local file="$1"
  local arg="$2"
  grep -Eq "(^|[[:space:]])${arg}([[:space:]]|$)" "$file"
}

echo "== Agent setup (Raspberry Pi OS) =="

apt_install curl ca-certificates

if cmdline_file="$(find_pi_cmdline)"; then
  need_reboot=0
  for arg in cgroup_memory=1 cgroup_enable=memory; do
    if ! cmdline_has_arg "$cmdline_file" "$arg"; then
      sudo_if_needed sed -i "s|$| $arg|" "$cmdline_file"
      need_reboot=1
    fi
  done

  if [[ "$need_reboot" -eq 1 ]]; then
    echo "Updated $cmdline_file with required cgroup args."
    echo "Reboot required before joining cluster."
    echo "Next: sudo reboot"
    exit 1
  fi
else
  echo "Warning: could not find Raspberry Pi cmdline.txt; skipped cgroup boot arg checks."
fi

echo "Agent setup complete."
