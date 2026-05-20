#!/usr/bin/env bash
# One-shot helper: record demo.cast → convert to docs/demo.gif
# Usage: bash scripts/make_demo.sh
#
# Requirements (install once):
#   brew install asciinema agg
#
# The resulting docs/demo.gif is committed to the repo and embedded in README.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAST="$REPO/docs/demo.cast"
GIF="$REPO/docs/demo.gif"

# ── preflight ─────────────────────────────────────────────────────────────────
for cmd in asciinema agg socc; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: '$cmd' not found in PATH"; exit 1; }
done

# ── record ────────────────────────────────────────────────────────────────────
echo "▶ Recording demo (cols=100, rows=36)…"
asciinema rec \
  --overwrite \
  --cols 100 \
  --rows 36 \
  -c "bash $REPO/scripts/demo_script.sh" \
  "$CAST"

# ── convert ───────────────────────────────────────────────────────────────────
echo "▶ Converting .cast → GIF…"
agg \
  --cols 100 \
  --rows 36 \
  --font-size 14 \
  --speed 1.1 \
  "$CAST" \
  "$GIF"

echo "✓ Done → $GIF"
echo "  Commit: git add docs/demo.cast docs/demo.gif && git commit -m 'docs: add demo GIF'"
