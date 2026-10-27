#!/usr/bin/env bash
# Install phase 1 on a Seeed reServer x86 (or any Ubuntu/Debian x86_64 box).
# Does not install a GPU driver, CUDA, or AMADEUS.
# Re-running is safe. It does not open the dashboard to the internet.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RUN_USER="${PRAWN_USER:-${SUDO_USER:-$(id -un)}}"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip ffmpeg v4l-utils
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    sudo apt-get install -y python3.11 python3.11-venv || true
  fi
else
  echo "Install Python 3.11+, venv, ffmpeg, and v4l-utils with the local package manager."
fi

pick_python() {
  local candidate
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  echo "Python 3.11 or newer is still missing." >&2
  return 1
}

PY="$(pick_python)"
if [[ "$(id -u)" -eq 0 ]]; then
  sudo -u "$RUN_USER" "$PY" -m venv "$ROOT/.venv"
  sudo -u "$RUN_USER" bash -c 'cd "$1" && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[usb]"' bash "$ROOT"
else
  "$PY" -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -U pip
  "$ROOT/.venv/bin/pip" install -e ".[usb]"
fi

mkdir -p "$ROOT/var/reserver/hub" "$ROOT/var/reserver/node"
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R "$RUN_USER:$RUN_USER" "$ROOT/var/reserver"
else
  sudo chown -R "$RUN_USER:$RUN_USER" "$ROOT/var/reserver" 2>/dev/null || true
fi

if command -v usermod >/dev/null 2>&1; then
  sudo usermod -aG dialout,video "$RUN_USER" || true
fi

sudo mkdir -p /etc/prawn-monitor
if [[ ! -f /etc/prawn-monitor/node.json ]]; then
  sudo cp "$ROOT/config/node.reserver.usb.example.json" /etc/prawn-monitor/node.json
  echo "Edit /etc/prawn-monitor/node.json and set each USB serial by-id path."
fi
if [[ ! -f /etc/prawn-monitor/hub.json ]]; then
  sudo cp "$ROOT/config/hub.reserver.json" /etc/prawn-monitor/hub.json
  echo "Edit /etc/prawn-monitor/hub.json public_url to this machine's LAN address."
fi
sudo chown "$RUN_USER:$RUN_USER" /etc/prawn-monitor/node.json /etc/prawn-monitor/hub.json || true

if [[ -d /etc/systemd/system ]]; then
  sed -e "s#@ROOT@#${ROOT}#g" -e "s#@USER@#${RUN_USER}#g" \
    "$ROOT/deploy/prawn-monitor-reserver.service" | sudo tee /etc/systemd/system/prawn-monitor-reserver.service >/dev/null
  sudo systemctl daemon-reload
  echo "After the USB paths are filled in:"
  echo "  sudo systemctl enable --now prawn-monitor-reserver.service"
else
  echo "No systemd here. Start by hand: ./scripts/reserver-up.sh --hardware"
fi

echo
echo "Phase 1 is sensors, camera, dashboard, and alerts on this machine."
echo "Phase 2 (AMADEUS on a GPU PC) is not installed and is not required."
echo "Try it before the probes are plugged in: ./scripts/reserver-up.sh"
echo "Dashboard, once it is up: http://<this-host>:8080"
echo "Log out and back in so the dialout and video groups apply to your shell."
