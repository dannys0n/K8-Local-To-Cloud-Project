#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

port_6443_listeners() {
  sudo_if_needed ss -ltnpH 2>/dev/null | awk '$4 ~ /:6443$/'
}

listener_pids_6443() {
  port_6443_listeners | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u
}

systemd_unit_for_pid() {
  local pid="$1"
  sudo_if_needed cat "/proc/$pid/cgroup" 2>/dev/null \
    | grep -oE '[^/[:space:]]+\.service' \
    | head -n1 || true
}

listener_units_6443() {
  local pid unit
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    unit="$(systemd_unit_for_pid "$pid")"
    [[ -n "$unit" ]] && printf '%s\n' "$unit"
  done < <(listener_pids_6443)
}

stop_systemd_unit() {
  local unit="$1"
  [[ -z "$unit" ]] && return 0
  echo "Stopping systemd unit owning tcp/6443: $unit"
  sudo_if_needed systemctl stop "$unit" || true
  sudo_if_needed systemctl disable "$unit" || true
  sudo_if_needed systemctl reset-failed "$unit" || true
}

free_port_6443() {
  if ! command -v ss >/dev/null 2>&1; then
    return 0
  fi

  mapfile -t units < <(listener_units_6443 | sort -u)
  if [[ "${#units[@]}" -gt 0 ]]; then
    for unit in "${units[@]}"; do
      stop_systemd_unit "$unit"
    done
    sleep 1
  fi

  mapfile -t pids < <(listener_pids_6443)
  if [[ "${#pids[@]}" -eq 0 ]]; then
    echo "tcp/6443 is already free."
    return 0
  fi

  echo "Releasing tcp/6443 listeners: ${pids[*]}"
  for pid in "${pids[@]}"; do
    [[ -z "$pid" ]] && continue
    if [[ "$pid" -le 1 ]]; then
      continue
    fi

    cmdline="$(sudo_if_needed cat "/proc/$pid/cmdline" 2>/dev/null | tr '\0' ' ' || true)"
    if [[ -n "$cmdline" ]]; then
      echo "Stopping PID $pid: $cmdline"
    else
      echo "Stopping PID $pid"
    fi
    sudo_if_needed kill -TERM "$pid" 2>/dev/null || true
  done

  sleep 2

  mapfile -t remaining < <(listener_pids_6443)
  if [[ "${#remaining[@]}" -gt 0 ]]; then
    echo "Force-killing remaining tcp/6443 listeners: ${remaining[*]}"
    for pid in "${remaining[@]}"; do
      [[ -z "$pid" ]] && continue
      if [[ "$pid" -le 1 ]]; then
        continue
      fi
      sudo_if_needed kill -KILL "$pid" 2>/dev/null || true
    done
    sleep 1
  fi

  if port_6443_listeners | grep -q .; then
    echo "Warning: tcp/6443 is still in use:"
    port_6443_listeners || true
  else
    echo "tcp/6443 is now free."
  fi
}

echo "== Server teardown =="

# Stop and reset service state first to prevent immediate auto-restarts.
sudo_if_needed systemctl stop k3s || true
sudo_if_needed systemctl disable k3s || true
sudo_if_needed systemctl reset-failed k3s || true

# Uninstall k3s if installed
if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
  sudo_if_needed /usr/local/bin/k3s-uninstall.sh || true
fi

# Clean up artifacts from older revisions of this repo.
sudo_if_needed systemctl stop k3s-lab-token || true
sudo_if_needed systemctl disable k3s-lab-token || true
sudo_if_needed rm -f /etc/systemd/system/k3s-lab-token.service || true
sudo_if_needed systemctl daemon-reload || true
sudo_if_needed rm -rf /opt/k3s-lab || true
rm -f "$HOME/.kube/config" "$HOME/.kube/config.bak" 2>/dev/null || true

free_port_6443

echo "Server teardown complete."
