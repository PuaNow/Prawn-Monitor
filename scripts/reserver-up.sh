#!/usr/bin/env bash
# Phase 1 on one x86 Linux machine (Seeed reServer / ODYSSEY Intel).
# Starts a local tank node and the hub. No GPU and no AMADEUS.
#
#   ./scripts/reserver-up.sh            simulated probes and camera
#   ./scripts/reserver-up.sh --check    same path, confirm the nursery, exit
#   ./scripts/reserver-up.sh --hardware USB config (/etc or the example)
#   ./scripts/reserver-up.sh --compose  docker compose up --build
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE=""
CHECK=0

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
}

choose() {
  local next="$1"
  if [[ -n "$MODE" && "$MODE" != "$next" ]]; then
    echo "Pass only one of --hardware or --compose." >&2
    exit 2
  fi
  MODE="$next"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --demo) choose demo ;;
    --hardware) choose hardware ;;
    --compose) choose compose ;;
    --check) CHECK=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done
MODE="${MODE:-demo}"

if [[ "$MODE" == compose ]]; then
  if [[ "$CHECK" -eq 1 ]]; then
    echo "The local check does not need Docker. Run: ./scripts/reserver-up.sh --check" >&2
    exit 2
  fi
  exec docker compose -f "$ROOT/docker-compose.yml" up --build
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
  echo "Python 3.11 or newer is required. Ubuntu 24.04 already has it. On 22.04: sudo apt-get install python3.11 python3.11-venv" >&2
  return 1
}

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  if ! "$(pick_python)" -m venv "$ROOT/.venv"; then
    rm -rf "$ROOT/.venv"
  fi
fi
if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" -c 'import sys' >/dev/null 2>&1; then
  PY="$ROOT/.venv/bin/python"
else
  rm -rf "$ROOT/.venv"
  PY="$(pick_python)"
  echo "Using ${PY} without a virtualenv. Install python3-venv for an isolated phase 1 environment." >&2
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required. Ubuntu 24.04 already has it. On 22.04: sudo apt-get install python3.11 python3.11-venv" >&2
  exit 1
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

if [[ "$MODE" == hardware ]]; then
  if ! "$PY" -c "import serial" >/dev/null 2>&1; then
    PIP="$ROOT/.venv/bin/pip"
    if [[ ! -x "$PIP" ]]; then
      PIP="$(command -v pip3 || true)"
    fi
    if [[ -z "$PIP" ]] || ! "$PIP" install -q 'pyserial>=3.5'; then
      echo "pyserial is missing. USB probes will show an error until you run sudo ./scripts/setup-reserver.sh." >&2
    fi
  fi
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is not installed. The dashboard will come up, but snapshots and clips will fail." >&2
fi

NODE_CFG="$ROOT/config/node.reserver.simulated.json"
HUB_CFG="$ROOT/config/hub.reserver.json"
if [[ "$MODE" == hardware ]]; then
  NODE_CFG="$ROOT/config/node.reserver.usb.example.json"
  if [[ -f /etc/prawn-monitor/node.json ]]; then
    NODE_CFG="/etc/prawn-monitor/node.json"
  fi
  if [[ -f /etc/prawn-monitor/hub.json ]]; then
    HUB_CFG="/etc/prawn-monitor/hub.json"
  fi
fi

NODE_PID=""
HUB_PID=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "$HUB_PID" ]] && kill -0 "$HUB_PID" 2>/dev/null; then
    kill "$HUB_PID" 2>/dev/null || true
    wait "$HUB_PID" 2>/dev/null || true
  fi
  if [[ -n "$NODE_PID" ]] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill "$NODE_PID" 2>/dev/null || true
    wait "$NODE_PID" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_http() {
  local url="$1"
  local pid="$2"
  local i
  for i in $(seq 1 50); do
    if "$PY" -c "import urllib.request; urllib.request.urlopen('${url}', timeout=1)" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Process ${pid} exited before ${url} answered." >&2
      return 1
    fi
    sleep 0.2
  done
  echo "Timed out waiting for ${url}." >&2
  return 1
}

echo "Phase 1 node (${MODE}) config ${NODE_CFG}"
"$PY" -m prawn_monitor node --config "$NODE_CFG" &
NODE_PID=$!
wait_http "http://127.0.0.1:8090/health" "$NODE_PID"

echo "Phase 1 hub config ${HUB_CFG}"
"$PY" -m prawn_monitor hub --config "$HUB_CFG" &
HUB_PID=$!
wait_http "http://127.0.0.1:8080/health" "$HUB_PID"

if [[ "$CHECK" -eq 1 ]]; then
  "$PY" - <<'PY'
import json
import urllib.request

urllib.request.urlopen(
    urllib.request.Request("http://127.0.0.1:8080/api/poll", data=b"{}", method="POST"),
    timeout=10,
).read()
overview = json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/overview", timeout=5))
tanks = {tank["id"]: tank for tank in overview["tanks"]}
nursery = tanks["nursery"]
if nursery.get("volume_gal") != 225:
    raise SystemExit(f"nursery volume was {nursery.get('volume_gal')}")
if not nursery.get("online"):
    raise SystemExit(f"nursery offline: {nursery.get('last_error')}")
reading = nursery.get("reading") or {}
for kind in ("dissolved_oxygen", "temperature", "ph", "water_level"):
    sample = reading.get(kind) or {}
    if not sample.get("ok"):
        raise SystemExit(f"{kind} was not ok: {sample}")
snap = urllib.request.urlopen(
    "http://127.0.0.1:8080/api/tanks/nursery/camera/snapshot.jpg",
    timeout=20,
)
body = snap.read()
if body[:2] != b"\xff\xd8":
    raise SystemExit("snapshot was not a JPEG")
do = reading["dissolved_oxygen"]["value"]
print(f"phase-1 reServer check ok: nursery {do} mg/L, snapshot {len(body)} bytes")
PY
  exit 0
fi

echo "Prawn Monitor phase 1 dashboard: http://127.0.0.1:8080"
echo "Phone on the farm LAN: http://<this-host>:8080  (do not forward the port)"
echo "Ctrl-C stops the hub and the node. AMADEUS is not running."
wait "$HUB_PID"
