#!/usr/bin/env bash
# scripts/build-sidecar-bin.sh — freeze the Python sidecar into a single
# self-contained binary and drop it where Tauri's externalBin expects it.
#
# End users get a distributable app with no uv/Python requirement: the frozen
# binary bundles the interpreter + all deps. Run from repo root (or anywhere).
#
# Output: src-tauri/binaries/prism-sidecar-<target-triple>[.exe]
#
# The heavy deps (litellm / yt-dlp / bilibili-api) are LAZILY imported and
# ship data files, so PyInstaller's static analysis misses them — hence the
# --collect-all flags. `uvicorn.run("prism_sidecar.app:app", ...)` is a
# dynamic import string, covered by --collect-submodules prism_sidecar.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY_DIR="$ROOT_DIR/python"
BIN_DIR="$ROOT_DIR/src-tauri/binaries"

# Target triple Tauri's externalBin convention wants in the filename.
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"
if [ -z "$TRIPLE" ]; then
  echo "error: could not determine host target triple (is rustc installed?)" >&2
  exit 1
fi

EXT=""
case "$TRIPLE" in
  *windows*) EXT=".exe" ;;
esac

echo "[build-sidecar] freezing for $TRIPLE …"

cd "$PY_DIR"
uv run pyinstaller \
  --onefile \
  --name prism-sidecar \
  --clean \
  --noconfirm \
  --collect-all litellm \
  --collect-all yt_dlp \
  --collect-all bilibili_api \
  --collect-all uvicorn \
  --collect-all tiktoken \
  --collect-submodules prism_sidecar \
  --hidden-import prism_sidecar.app \
  --hidden-import uvloop \
  --hidden-import httptools \
  --hidden-import tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  --copy-metadata tiktoken \
  packaging/entry.py

mkdir -p "$BIN_DIR"
OUT="$BIN_DIR/prism-sidecar-${TRIPLE}${EXT}"
cp "$PY_DIR/dist/prism-sidecar${EXT}" "$OUT"
chmod +x "$OUT"

SIZE="$(du -h "$OUT" | cut -f1)"
echo "[build-sidecar] done → $OUT ($SIZE)"
