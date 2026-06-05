#!/usr/bin/env bash
# scripts/smoke.sh — End-to-end smoke test for the Prism sidecar.
#
# Starts the Python sidecar in the background, waits for it to become healthy,
# pings a few endpoints, validates the JSON shape, and tears the process down.
#
# Exits 0 on success, 1 on any failure. Any sidecar process started by this
# script is killed on exit (including on SIGINT/SIGTERM or any earlier bail).

set -euo pipefail

# ----- Paths & config ----------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY_DIR="$ROOT_DIR/python"

SIDECAR_HOST="${SIDECAR_HOST:-127.0.0.1}"
SIDECAR_PORT="${SIDECAR_PORT:-8765}"
BASE_URL="http://${SIDECAR_HOST}:${SIDECAR_PORT}"
HEALTH_URL="${BASE_URL}/health"
SOURCES_URL="${BASE_URL}/api/sources"
ITEMS_URL="${BASE_URL}/api/items"

HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-15}"

SIDECAR_PID=""
SIDECAR_LOG=""

# ----- Cleanup trap ------------------------------------------------------

cleanup() {
  local exit_code=$?
  # Kill the original launcher pid (uv wrapper / shell).
  if [[ -n "${SIDECAR_PID:-}" ]] && kill -0 "$SIDECAR_PID" 2>/dev/null; then
    log "stopping sidecar (pid=$SIDECAR_PID)"
    kill -TERM "$SIDECAR_PID" 2>/dev/null || true
  fi
  # Belt-and-suspenders: even if SIGTERM on the wrapper doesn't propagate
  # to the actual uvicorn worker (some launchers fork a grandchild that
  # doesn't share the same process group), hunt down anything still
  # listening on our port and kill it too.
  if command -v lsof >/dev/null 2>&1; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      listeners=$(lsof -nP -iTCP:"$SIDECAR_PORT" -sTCP:LISTEN -t 2>/dev/null || true)
      [[ -z "$listeners" ]] && break
      # shellcheck disable=SC2086
      kill -TERM $listeners 2>/dev/null || true
      sleep 0.3
    done
    listeners=$(lsof -nP -iTCP:"$SIDECAR_PORT" -sTCP:LISTEN -t 2>/dev/null || true)
    if [[ -n "$listeners" ]]; then
      log "force-killing stubborn port listeners: $listeners"
      # shellcheck disable=SC2086
      kill -9 $listeners 2>/dev/null || true
    fi
  fi
  # Final check on the original pid.
  if [[ -n "${SIDECAR_PID:-}" ]] && kill -0 "$SIDECAR_PID" 2>/dev/null; then
    kill -9 "$SIDECAR_PID" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log()  { printf '[smoke] %s\n' "$*"; }
fail() { printf '[smoke][fail] %s\n' "$*" >&2; exit 1; }

# ----- Helpers -----------------------------------------------------------

# jget <json-string> <python-expr-on-data>
#   Extract a value from a JSON document. Always uses python3 (no new deps).
#   e.g.  jget "$body" "data['ok']"
#         jget "$body" "len(data)"
#         jget "$body" "data[0]['name']"
jget() {
  local body="$1"
  local expr="$2"
  python3 -c "
import json, sys
expr = sys.argv[1]
data = json.loads(sys.stdin.read())
result = eval(expr)
if isinstance(result, bool):
    print('true' if result else 'false')
elif result is None:
    print('null')
else:
    print(result)
" "$expr" <<<"$body"
}

# ----- Pre-flight checks -------------------------------------------------

[[ -d "$PY_DIR" ]] || fail "python/ not found at $PY_DIR — run from the repo root"
[[ -f "$PY_DIR/pyproject.toml" ]] || fail "$PY_DIR/pyproject.toml missing — is this the Prism repo?"

# Port conflict detection: if something is already listening on the port, bail.
if command -v lsof >/dev/null 2>&1; then
  existing=$(lsof -nP -iTCP:"$SIDECAR_PORT" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "$existing" ]]; then
    fail "port $SIDECAR_PORT already in use by pid(s): $existing — stop them first (e.g. \`lsof -nP -iTCP:$SIDECAR_PORT\` and \`kill\`)"
  fi
elif command -v ss >/dev/null 2>&1; then
  if ss -lnt "sport = :$SIDECAR_PORT" 2>/dev/null | awk 'NR>1 {found=1} END {exit !found}'; then
    fail "port $SIDECAR_PORT already in use — stop the listener first"
  fi
fi

# Pick runner: uv preferred, python3 -m fallback.
if command -v uv >/dev/null 2>&1; then
  RUNNER=(uv run prism-sidecar --host "$SIDECAR_HOST" --port "$SIDECAR_PORT")
  RUNNER_CWD="$PY_DIR"
  RUNNER_LABEL="uv run prism-sidecar"
else
  if ! command -v python3 >/dev/null 2>&1; then
    fail "neither 'uv' nor 'python3' is on PATH — install one to run the smoke test"
  fi
  RUNNER=(python3 -m prism_sidecar --host "$SIDECAR_HOST" --port "$SIDECAR_PORT")
  RUNNER_CWD="$PY_DIR"
  RUNNER_LABEL="python3 -m prism_sidecar"
fi

# SMOKE_RUNNER override: space-separated command. Useful for forcing a
# specific runner in CI / debug scenarios. Leave unset in normal use.
if [[ -n "${SMOKE_RUNNER:-}" ]]; then
  # shellcheck disable=SC2206
  RUNNER=( $SMOKE_RUNNER )
  RUNNER_LABEL="SMOKE_RUNNER override: $SMOKE_RUNNER"
fi

# ----- Start the sidecar -------------------------------------------------

log "starting sidecar via $RUNNER_LABEL on $BASE_URL"
SIDECAR_LOG="$(mktemp -t prism-smoke.XXXXXX.log)"
(
  cd "$RUNNER_CWD"
  "${RUNNER[@]}"
) >"$SIDECAR_LOG" 2>&1 &
SIDECAR_PID=$!
log "sidecar pid=$SIDECAR_PID, log=$SIDECAR_LOG"

# ----- Wait for /health --------------------------------------------------

log "waiting for $HEALTH_URL (timeout=${HEALTH_TIMEOUT_SEC}s)"
elapsed=0
while (( elapsed < HEALTH_TIMEOUT_SEC )); do
  if ! kill -0 "$SIDECAR_PID" 2>/dev/null; then
    log "sidecar exited prematurely. Log tail:"
    tail -n 40 "$SIDECAR_LOG" >&2 || true
    SIDECAR_PID=""   # trap will see no live pid
    fail "sidecar process died before becoming healthy (try: cd $PY_DIR && uv sync)"
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    log "sidecar is healthy after ${elapsed}s"
    break
  fi
  sleep 1
  elapsed=$((elapsed + 1))
done

if (( elapsed >= HEALTH_TIMEOUT_SEC )); then
  log "sidecar did not become healthy within ${HEALTH_TIMEOUT_SEC}s. Log tail:"
  tail -n 40 "$SIDECAR_LOG" >&2 || true
  fail "health check timed out"
fi

# ----- Endpoint checks ---------------------------------------------------

log "GET $HEALTH_URL"
health=$(curl -fsS --max-time 5 "$HEALTH_URL")
ok=$(jget "$health" "data['ok']")
[[ "$ok" == "true" ]] || fail "/health .ok expected 'true', got '$ok'"
version=$(jget "$health" "data['version']")
[[ -n "$version" && "$version" != "null" ]] || fail "/health .version missing"
sources_count=$(jget "$health" "data['sourcesCount']")
[[ "$sources_count" =~ ^[0-9]+$ ]] && (( sources_count >= 1 )) || fail "/health .sourcesCount not a positive int (got '$sources_count')"
# v0.2a: itemsCount is 0 until a sync runs (no in-memory pre-populated items).
# We do NOT require itemsCount >= 1 here; instead we trigger /api/sync below
# and re-check. The legacy v0.1 smoke asserted itemsCount >= 1 because
# fixtures pre-populated items — that no longer applies.
items_count=$(jget "$health" "data['itemsCount']")
[[ "$items_count" =~ ^[0-9]+$ ]] || fail "/health .itemsCount not an int (got '$items_count')"
log "/health OK  version=$version  sources=$sources_count  items=$items_count (pre-sync)"

log "GET $SOURCES_URL"
sources_body=$(curl -fsS --max-time 5 "$SOURCES_URL")
sources_len=$(jget "$sources_body" "len(data)")
(( sources_len >= 1 )) || fail "/api/sources expected >=1 item, got $sources_len"
first_id=$(jget "$sources_body" "data[0]['id']")
first_name=$(jget "$sources_body" "data[0]['name']")
first_kind=$(jget "$sources_body" "data[0]['kind']")
[[ -n "$first_id"   && "$first_id"   != "null" ]] || fail "/api/sources[0].id missing"
[[ -n "$first_name" && "$first_name" != "null" ]] || fail "/api/sources[0].name missing"
[[ -n "$first_kind" && "$first_kind" != "null" ]] || fail "/api/sources[0].kind missing"
log "/api/sources OK  count=$sources_len  first=$first_id ($first_name, $first_kind)"

# v0.2a: items now come from real fetches. Trigger a sync and verify items
# get persisted. We do this for a SINGLE source (first_id) to keep the
# smoke fast — /api/sync without args would fetch all sources and could
# take >30s. Verifier will exercise the full-pipeline path separately.
log "POST ${BASE_URL}/api/sync/$first_id (single-source sync, fast)"
sync_body=$(curl -fsS --max-time 60 -X POST "${BASE_URL}/api/sync/$first_id" || true)
if [[ -z "$sync_body" ]]; then
  log "WARN: single-source sync returned no body; falling back to /api/items assertion without sync"
else
  sync_status=$(jget "$sync_body" "data['status']")
  sync_items_new=$(jget "$sync_body" "data['itemsNew']")
  log "/api/sync/$first_id OK  status=$sync_status  itemsNew=$sync_items_new"
fi

log "GET $ITEMS_URL"
items_body=$(curl -fsS --max-time 5 "$ITEMS_URL")
items_len=$(jget "$items_body" "len(data)")
(( items_len >= 1 )) || fail "/api/items expected >=1 item after sync, got $items_len"
item_id=$(jget "$items_body" "data[0]['id']")
item_title=$(jget "$items_body" "data[0]['title']")
item_source_id=$(jget "$items_body" "data[0]['sourceId']")
[[ -n "$item_id"        && "$item_id"        != "null" ]] || fail "/api/items[0].id missing"
[[ -n "$item_title"     && "$item_title"     != "null" ]] || fail "/api/items[0].title missing"
[[ -n "$item_source_id" && "$item_source_id" != "null" ]] || fail "/api/items[0].sourceId missing"
log "/api/items OK  count=$items_len  first=$item_id"

# ----- Deep check: GET /api/sources/{id} --------------------------------

log "GET ${BASE_URL}/api/sources/$first_id"
deep_body=$(curl -fsS --max-time 5 "${BASE_URL}/api/sources/$first_id")
deep_kind=$(jget "$deep_body" "data['kind']")
[[ "$deep_kind" == "$first_kind" ]] || fail "/api/sources/$first_id .kind='$deep_kind', expected '$first_kind'"
deep_id=$(jget "$deep_body" "data['id']")
[[ "$deep_id" == "$first_id" ]] || fail "/api/sources/$first_id .id='$deep_id', expected '$first_id'"
log "/api/sources/$first_id OK"

log "all smoke checks passed"
exit 0
