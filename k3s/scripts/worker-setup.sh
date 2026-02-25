#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

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

echo "== Worker setup (Raspberry Pi OS) =="

apt_install curl ca-certificates

# Ensure iptables tooling is present (k3s/CNI expects iptables-save/restore).
# Raspberry Pi OS images sometimes omit these by default.
ensure_iptables_tools() {
  if command -v iptables-save >/dev/null 2>&1 && command -v iptables-restore >/dev/null 2>&1; then
    return 0
  fi
  echo "[INFO] Installing iptables tools..."
  apt_install iptables
}

query_alt_value() {
  local name="$1"
  if ! command -v update-alternatives >/dev/null 2>&1; then
    return 1
  fi
  update-alternatives --query "$name" 2>/dev/null | awk -F': ' '$1=="Value"{print $2}'
}

set_alt_value() {
  local name="$1"
  local value="$2"
  sudo_if_needed update-alternatives --set "$name" "$value"
}

maybe_configure_iptables_mode() {
  # Best practice: keep the host default (usually nft). Only switch when you have CNI issues.
  # Set K3S_IPTABLES_MODE=legacy to force legacy mode, or K3S_IPTABLES_MODE=nft to force nft.
  local mode="${K3S_IPTABLES_MODE:-auto}"
  mode="$(echo "$mode" | tr '[:upper:]' '[:lower:]')"
  if [[ "$mode" == "auto" || -z "$mode" ]]; then
    return 0
  fi

  local orig4 orig6 target4 target6
  orig4="$(query_alt_value iptables || true)"
  orig6="$(query_alt_value ip6tables || true)"
  if [[ -z "$orig4" || -z "$orig6" ]]; then
    echo "[WARN] update-alternatives entries for iptables not found; skipping mode switch."
    return 0
  fi

  case "$mode" in
    legacy)
      target4="/usr/sbin/iptables-legacy"
      target6="/usr/sbin/ip6tables-legacy"
      ;;
    nft)
      target4="/usr/sbin/iptables-nft"
      target6="/usr/sbin/ip6tables-nft"
      ;;
    *)
      echo "[WARN] Unknown K3S_IPTABLES_MODE='$mode' (use auto|nft|legacy)."
      return 0
      ;;
  esac

  if [[ ! -x "$target4" || ! -x "$target6" ]]; then
    echo "[WARN] Target binaries not found ($target4 / $target6); skipping mode switch."
    return 0
  fi

  if [[ "$orig4" == "$target4" && "$orig6" == "$target6" ]]; then
    return 0
  fi

  local conf_dir="/etc/k3s-lab"
  local state_file="${conf_dir}/iptables-mode.env"
  sudo_if_needed mkdir -p "$conf_dir"
  printf 'IPTABLES_ORIG_VALUE=%q\nIP6TABLES_ORIG_VALUE=%q\n' "$orig4" "$orig6" | sudo_if_needed tee "$state_file" >/dev/null
  sudo_if_needed chmod 0600 "$state_file"

  echo "[INFO] Switching iptables to '${mode}' mode."
  set_alt_value iptables "$target4"
  set_alt_value ip6tables "$target6"
}

ensure_iptables_tools
maybe_configure_iptables_mode


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

echo "Worker setup complete."
