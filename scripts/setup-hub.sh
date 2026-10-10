#!/usr/bin/env bash
# Install the hub onto a Linux machine that stays on the farm LAN.
# Re-running is safe. It does not open the dashboard to the internet.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip ffmpeg
else
  echo "Install Python 3.11+, venv, and ffmpeg with the local package manager."
fi

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -U pip
"$ROOT/.venv/bin/pip" install -e "$ROOT"

install_dir() {
  local dest="$1"
  if [[ -d "$dest" ]]; then
    return
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    mkdir -p "$dest"
  else
    echo "Create ${dest} as root, or keep using ${ROOT}/var/hub from the example config."
  fi
}

install_dir /etc/prawn-monitor
install_dir /var/lib/prawn-monitor/hub
if [[ "$(id -u)" -eq 0 && ! -f /etc/prawn-monitor/hub.json ]]; then
  cp "$ROOT/config/hub.example.json" /etc/prawn-monitor/hub.json
  echo "Edit /etc/prawn-monitor/hub.json and set each tank node_url."
fi

if [[ "$(id -u)" -eq 0 && -d /etc/systemd/system ]]; then
  cp "$ROOT/deploy/prawn-monitor-hub.service" /etc/systemd/system/
  systemctl daemon-reload
  echo "When the config is filled in: systemctl enable --now prawn-monitor-hub.service"
else
  echo "Start it by hand:"
  echo "  PYTHONPATH=src .venv/bin/python -m prawn_monitor hub --config config/hub.example.json"
fi
