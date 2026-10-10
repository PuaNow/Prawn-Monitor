#!/usr/bin/env bash
# Install a tank node on a Raspberry Pi. Optional: --interfaces appends I2C
# and 1-Wire lines to the Pi boot config when they are missing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENABLE_INTERFACES=0
if [[ "${1:-}" == "--interfaces" ]]; then
  ENABLE_INTERFACES=1
fi

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip ffmpeg i2c-tools python3-smbus
else
  echo "Install Python 3.11+, ffmpeg, and i2c-tools with the local package manager."
fi

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -U pip
"$ROOT/.venv/bin/pip" install -e "$ROOT"
"$ROOT/.venv/bin/pip" install smbus2 || echo "smbus2 install failed; simulated mode still works."

if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p /etc/prawn-monitor /var/lib/prawn-monitor/nursery
  if [[ ! -f /etc/prawn-monitor/node.json ]]; then
    cp "$ROOT/config/node.atlas.example.json" /etc/prawn-monitor/node.json
    echo "Edit /etc/prawn-monitor/node.json. Use node.dfrobot.example.json for the analog build."
  fi
  if [[ -d /etc/systemd/system ]]; then
    cp "$ROOT/deploy/prawn-monitor-node.service" /etc/systemd/system/
    systemctl daemon-reload
    echo "After wiring: systemctl enable --now prawn-monitor-node.service"
  fi
fi

enable_boot() {
  local file=""
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "$candidate" ]]; then
      file="$candidate"
      break
    fi
  done
  if [[ -z "$file" ]]; then
    echo "No Pi boot config found. On a Pi, add these lines to config.txt:"
    echo "  dtparam=i2c_arm=on"
    echo "  dtoverlay=w1-gpio"
    return
  fi
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Re-run with sudo to edit ${file}, or add the lines yourself:"
    echo "  dtparam=i2c_arm=on"
    echo "  dtoverlay=w1-gpio"
    return
  fi
  cp "$file" "${file}.prawn-bak"
  grep -q '^dtparam=i2c_arm=on' "$file" || echo 'dtparam=i2c_arm=on' >> "$file"
  grep -q '^dtoverlay=w1-gpio' "$file" || echo 'dtoverlay=w1-gpio' >> "$file"
  echo "Updated ${file}. Reboot before the probes will answer."
}

if [[ "$ENABLE_INTERFACES" -eq 1 ]]; then
  enable_boot
else
  echo "Pass --interfaces on a Raspberry Pi to enable I2C and 1-Wire in config.txt."
fi

echo "Check the bus after reboot: i2cdetect -y 1"
echo "Atlas DO is 0x61, Atlas pH is 0x63, ADS1115 is 0x48."
