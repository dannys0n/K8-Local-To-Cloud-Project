\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

echo "== Worker setup (Raspberry Pi OS) =="

apt_install curl ca-certificates avahi-daemon libnss-mdns

sudo_if_needed systemctl enable --now avahi-daemon || true

# Enable cgroups (common requirement on Pi OS). Best-effort.
CMDLINE=""
if [[ -f /boot/cmdline.txt ]]; then CMDLINE="/boot/cmdline.txt"; fi
if [[ -f /boot/firmware/cmdline.txt ]]; then CMDLINE="/boot/firmware/cmdline.txt"; fi

if [[ -n "$CMDLINE" ]]; then
  NEED_REBOOT=0
  for p in cgroup_enable=cpuset cgroup_memory=1 cgroup_enable=memory; do
    if ! grep -q "$p" "$CMDLINE"; then
      sudo_if_needed sed -i "s|$| $p|" "$CMDLINE"
      NEED_REBOOT=1
    fi
  done

  if [[ "$NEED_REBOOT" -eq 1 ]]; then
    echo "Enabled cgroups in $CMDLINE. Rebooting now..."
    sudo_if_needed reboot
    exit 0
  fi
fi

echo "Worker setup complete."
