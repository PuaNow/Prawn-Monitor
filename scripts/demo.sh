#!/usr/bin/env bash
# Simulated nursery demo. With no arguments it runs the check and exits.
# Pass --serve to leave the dashboard running.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install -q pytest
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ $# -eq 0 ]]; then
  set -- --check
fi
exec "$ROOT/.venv/bin/python" -m prawn_monitor demo "$@"
