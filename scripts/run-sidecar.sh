#!/usr/bin/env bash
# Start the Python sidecar in the foreground.
# Used by `npm run sidecar:dev` and by Tauri to auto-spawn on startup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY_DIR="$ROOT_DIR/python"

# Sanity check
if [ ! -d "$PY_DIR" ]; then
  echo "[prism] python/ directory not found at $PY_DIR" >&2
  exit 1
fi

# uv is the recommended runner; fall back to python3 -m if uv is missing
if command -v uv >/dev/null 2>&1; then
  echo "[prism] starting sidecar via uv"
  cd "$ROOT_DIR"
  exec uv --directory "$PY_DIR" run prism-sidecar "$@"
else
  echo "[prism] uv not found, falling back to python3 -m"
  echo "[prism] (install uv for the proper experience: https://docs.astral.sh/uv/)"
  cd "$PY_DIR"
  exec python3 -m prism_sidecar "$@"
fi
