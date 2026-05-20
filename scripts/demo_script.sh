#!/usr/bin/env bash
# Scripted demo for asciinema recording.
# Run via: asciinema rec --overwrite --cols 100 --rows 36 \
#               -c "bash scripts/demo_script.sh" docs/demo.cast
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJ_DIR"

# ── helpers ──────────────────────────────────────────────────────────────────
_type() {
  # Print a green prompt then simulate typing the command character-by-character
  printf '\033[1;32m❯\033[0m '
  local s="$1"
  for ((i = 0; i < ${#s}; i++)); do
    printf '%s' "${s:$i:1}"
    sleep 0.045
  done
  printf '\n'
}

run() {
  _type "$1"
  sleep 0.2
  eval "$1"
}

# ── demo ─────────────────────────────────────────────────────────────────────
# Use a neutral temp path so the recording doesn't embed the developer's home dir
cp "$PROJ_DIR/data/examples/rk3588s-orangepi-5.dts" /tmp/board.dts

clear
sleep 0.8

run "socc --version"
sleep 1.2

cd /tmp
run "socc check board.dts --soc rk3588 --no-cache 2>/dev/null | head -52"
sleep 4
