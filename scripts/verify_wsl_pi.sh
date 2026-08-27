#!/usr/bin/env bash
# End-to-end: Linux pi spawns Windows venv Python and completes ACP initialize.
set -euo pipefail

export PI_HOME="${PI_HOME:-/mnt/c/Users/c1055/.pi-python}"
export PI_USE_MOCK=1

PI_BIN="${PI_BIN:-$HOME/grok-build-target/release/pi}"
REPO="${REPO:-/mnt/d/work/pi-python}"

cd "$REPO"

echo "== pi -p headless =="
out="$("$PI_BIN" -p "reply with exactly: pi-ready" 2>&1)" || {
  echo "FAIL: pi -p exited non-zero"
  echo "$out"
  exit 1
}
echo "$out"
echo "$out" | grep -q pi-ready || {
  echo "FAIL: expected pi-ready in output"
  exit 1
}

echo "== ACP stdio smoke =="
/mnt/d/work/pi-python/.venv-tui/Scripts/python.exe "$REPO/scripts/test_acp_stdio.py"

echo "OK: pi WSL spawn path verified"
