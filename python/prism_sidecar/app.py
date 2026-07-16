"""FastAPI app — Prism sidecar (v0.2a).

Major changes from v0.1:
- SQLite-backed via `prism_sidecar.store`
- Real /api/sync that runs the fetch + distill pipeline
- New endpoints: PATCH /api/sources/{id}, /api/sync/{id}, /api/sync/{job_id},
  /api/sync/history
- APScheduler kicks off a daily 9 AM Asia/Shanghai sync
- Lifespan: opens the DB, seeds default sources, starts scheduler,
  shuts everything down cleanly on exit
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from prism_sidecar import __version__, _http, scheduler, search, settings, store
from prism_sidecar.progress import progress_store
from prism_sidecar.config import (
    DAILY_SYNC_ENABLED,
    DAILY_SYNC_HOUR,
    DAILY_SYNC_TZ,
    PRISM_DB_PATH,
    SHUTDOWN_GRACE_SEC,
)
from prism_sidecar.data.fixtures import SEED_SOURCES
from prism_sidecar.db import close_db, init_db
from prism_sidecar.distillers.registry import get_distiller as _registry_get_distiller
from prism_sidecar.models import (
    HealthInfo,
    ItemStatus,
    ItemStatusPatch,
    ItemTagCreate,
    KnowledgeItem,
    Source,
    SourceCreate,
    SourcePatch,
    SyncJobStatus,
    SyncLogEntry,
    SyncResult,
    TagCount,
)
from prism_sidecar.pipeline import orchestrator
from prism_sidecar.pipeline.distill import list_pending_distill_ids, redistill_all_pending

# ---- Logging -------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("prism-sidecar")


# ---- Sync-job orchestration ----------------------------------------------
#
# The concurrency control (in-flight/cancelled job tracking) and the
# actual "run the pipeline across N sources, track progress, handle
# cancel" logic used to live inline here. It moved to
# `pipeline/orchestrator.py` — this file grew past 900 lines mixing
# route handlers with that orchestration, and the split mirrors the
# existing `pipeline/distill.py` pattern for the redistill batch logic.
#
# `_inflight_jobs` and `run_all_sync_background` are re-exported here
# (not copies — same underlying `set` / function object) for two
# external contracts that named this module specifically:
#   * `tests/test_api.py` asserts on `app._inflight_jobs` directly.
#   * `scheduler.py` does a late `from prism_sidecar.app import
#     run_all_sync_background` to avoid an import cycle.
# New code should prefer calling `orchestrator.*` directly.
_inflight_jobs = orchestrator.inflight_jobs
run_all_sync_background = orchestrator.run_all_sync_background
run_failed_retry_background = orchestrator.run_failed_retry_background

# Cached reference to the most recently built distiller. The pipeline
# builds a fresh distiller per job (so config changes between jobs are
# picked up), but the /api/settings/llm endpoint can hot-swap this
# reference for callers that want to test the change immediately.
#
# NOTE: the hot-swap is best-effort — a "may help for quick UI feedback"
# nicety, not a guarantee. The reliable way to apply a provider change is
# to restart the sidecar so it re-reads its env. Since v0.2c that is a
# first-class user action (`sidecar::restart_sidecar`, surfaced as the
# "Apply & Restart Sidecar" button in Settings), so this is no longer a
# TODO — it's the documented division of labour.
_current_distiller: object | None = None

# ---- Lifespan ------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("[prism-sidecar] v%s starting up", __version__)
    log.info("[prism-sidecar] db: %s", PRISM_DB_PATH)

    # Read the active provider config (or create the default file).
    active = settings.write_default_if_missing()
    log.info(
        "[prism-sidecar] active LLM provider: %s (model=%s, base_url=%s)",
        active["provider"],
        active.get("model"),
        settings.resolve_base_url(active["provider"], active.get("base_url"))
        or "<provider default>",
    )
    log.info(
        "[prism-sidecar] distiller: %s",
        "configured" if settings.is_provider_configured(active["provider"])
        else f"NOT configured ({active['provider']} env key missing)",
    )

    await init_db()
    # Crash recovery: a job row left in 'running' by a killed sidecar
    # would make is_any_job_running() true forever and 409 every
    # future sync. No job can be running this early in startup, so
    # flip the orphans to 'error' before anything else looks at them.
    await store.fail_orphan_running_jobs()
    await store.ensure_default_sources(SEED_SOURCES)

    if DAILY_SYNC_ENABLED:
        scheduler.start_scheduler()
    else:
        log.info("[prism-sidecar] daily sync disabled via env")

    orchestrator.is_app_ready = True
    log.info(
        "[prism-sidecar] ready on http://127.0.0.1:8765 (daily_sync=%02d:00 %s)",
        DAILY_SYNC_HOUR, DAILY_SYNC_TZ,
    )

    try:
        yield
    finally:
        orchestrator.is_app_ready = False
        log.info("[prism-sidecar] shutting down")
        # Order matters: stop the scheduler first (no NEW jobs), then
        # drain in-flight sync work (jobs stop at their next per-source
        # checkpoint and persist partial progress), THEN close the db —
        # closing it under a mid-write pipeline would corrupt the very
        # progress we're trying to save. See orchestrator.drain_inflight.
        scheduler.shutdown_scheduler()
        await orchestrator.drain_inflight(SHUTDOWN_GRACE_SEC)
        # v0.5.x: redistill runs as a background task — cancel it before
        # closing the DB. Each distilled item commits its own write, so
        # cancellation loses at most the one item currently in flight.
        if _redistill_task is not None and not _redistill_task.done():
            _redistill_task.cancel()
            await asyncio.gather(_redistill_task, return_exceptions=True)
        await close_db()
        # Close the shared httpx client last — fetch/distill work above
        # may still have been flushing requests through it.
        await _http.aclose_current()


# ---- App -----------------------------------------------------------------

app = FastAPI(
    title="Prism Sidecar",
    version=__version__,
    description="AI news & knowledge distillation sidecar",
    lifespan=lifespan,
)

# Allow the Tauri webview (and Vite dev server) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _require_api_token(request: Request, call_next):
    """Loopback auth (v0.5.x).

    CORS only protects against *browsers* — any local process could
    otherwise create/delete sources or register a data-exfiltrating
    webhook on 127.0.0.1:8765. When the Tauri shell spawns us it
    injects a per-app-run random token as PRISM_API_TOKEN (see
    sidecar.rs) and sends it back on every request; we require it here.

    Details:
      * Token read from env PER REQUEST (cheap dict lookup) so tests
        can monkeypatch os.environ and dev runs (`uv run`, no env set)
        keep the check off entirely.
      * Accepted as the `X-Prism-Token` header, or `?token=` for the
        SSE endpoint — EventSource cannot set custom headers.
      * OPTIONS passes through: CORS preflights never carry custom
        headers, and this middleware wraps the CORSMiddleware (added
        above = inner), so it sees the preflight first.
      * /health stays open: counts + version only, and keeping it
        token-free preserves curl-level debuggability.
    """
    token = os.environ.get("PRISM_API_TOKEN")
    if not token or request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    supplied = request.headers.get("x-prism-token") or request.query_params.get("token")
    if not supplied or not hmac.compare_digest(supplied, token):
        return JSONResponse(
            {"detail": "missing or invalid API token"}, status_code=401
        )
    return await call_next(request)


# ---- Health --------------------------------------------------------------

@app.get("/health", response_model=HealthInfo, response_model_by_alias=True)
async def health() -> HealthInfo:
    snap = await store.health_snapshot()
    return HealthInfo(**snap)


# ---- Sources -------------------------------------------------------------

@app.get("/api/sources", response_model=list[Source], response_model_by_alias=True)
async def list_sources() -> list[Source]:
    return await store.list_sources()


@app.get("/api/sources/{source_id}", response_model=Source, response_model_by_alias=True)
async def get_source(source_id: str) -> Source:
    s = await store.get_source(source_id)
    if not s:
        raise HTTPException(404, f"source {source_id} not found")
    return s


@app.post("/api/sources", response_model=Source, response_model_by_alias=True)
async def create_source(payload: SourceCreate) -> Source:
    return await store.create_source(
        name=payload.name,
        kind=payload.kind.value,
        url=payload.url,
        enabled=payload.enabled,
        config_json=payload.config_json,
    )


@app.patch("/api/sources/{source_id}", response_model=Source, response_model_by_alias=True)
async def patch_source(source_id: str, payload: SourcePatch) -> Source:
    existing = await store.get_source(source_id)
    if not existing:
        raise HTTPException(404, f"source {source_id} not found")
    updated = await store.patch_source(
        source_id,
        name=payload.name,
        url=payload.url,
        enabled=payload.enabled,
        config_json=payload.config_json,
    )
    assert updated is not None
    return updated


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str) -> dict:
    ok = await store.delete_source(source_id)
    if not ok:
        raise HTTPException(404, f"source {source_id} not found")
    return {"ok": True}


# ---- Items ---------------------------------------------------------------

@app.get("/api/items", response_model=list[KnowledgeItem], response_model_by_alias=True)
async def list_items(
    source_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[KnowledgeItem]:
    return await store.list_items(
        source_id=source_id, status=status, q=q, tag=tag, limit=limit, offset=offset,
    )


@app.get("/api/items/{item_id}", response_model=KnowledgeItem, response_model_by_alias=True)
async def get_item(item_id: str) -> KnowledgeItem:
    it = await store.get_item(item_id)
    if not it:
        raise HTTPException(404, f"item {item_id} not found")
    return it


@app.patch(
    "/api/items/{item_id}",
    response_model=KnowledgeItem,
    response_model_by_alias=True,
)
async def patch_item_status(item_id: str, payload: ItemStatusPatch) -> KnowledgeItem:
    """Set an item's status (unread / read / starred / archived).

    This is the write path behind the inbox's status filters — the
    filters (and the `ItemStatusPatch` model) existed since v0.1 but
    there was no endpoint to actually change a status, so `starred` /
    `archived` were permanently empty.
    """
    updated = await store.update_item_status(item_id, payload.status)
    if not updated:
        raise HTTPException(404, f"item {item_id} not found")
    return updated


# ---- Tags (v0.5) ---------------------------------------------------------

@app.get("/api/tags", response_model=list[TagCount], response_model_by_alias=True)
async def list_tags() -> list[TagCount]:
    """Every user tag with its item count — powers the inbox tag filter."""
    return await store.list_user_tags()


@app.post(
    "/api/items/{item_id}/tags",
    response_model=KnowledgeItem,
    response_model_by_alias=True,
)
async def add_item_tag(item_id: str, payload: ItemTagCreate) -> KnowledgeItem:
    """Attach a user tag to an item. Idempotent; 400 on an invalid tag."""
    try:
        updated = await store.add_item_tag(item_id, payload.tag)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, f"item {item_id} not found")
    return updated


@app.delete(
    "/api/items/{item_id}/tags/{tag}",
    response_model=KnowledgeItem,
    response_model_by_alias=True,
)
async def remove_item_tag(item_id: str, tag: str) -> KnowledgeItem:
    """Remove a user tag from an item. Idempotent."""
    updated = await store.remove_item_tag(item_id, tag)
    if not updated:
        raise HTTPException(404, f"item {item_id} not found")
    return updated


# ---- Semantic search (v0.5) ----------------------------------------------

@app.get("/api/search/status")
async def search_status() -> dict:
    """Whether semantic search is available + how much is indexed/pending."""
    return await search.search_status()


@app.post("/api/search/reindex")
async def search_reindex(batch_limit: int = Query(500, ge=1, le=1000)) -> dict:
    """Embed distilled items missing a vector (best-effort, idempotent).

    Default batch of 500 keeps one request bounded (~16 embedding API
    calls); the response's `remaining` count tells the client whether
    another pass is needed, and the UI's reindex button stays visible
    until it hits zero. (Unbounded used to be the default, which let a
    big backlog pin the HTTP request for minutes.)
    """
    return await search.reindex_missing(batch_limit=batch_limit)


@app.get(
    "/api/search/semantic",
    response_model=list[KnowledgeItem],
    response_model_by_alias=True,
)
async def search_semantic(
    q: str,
    limit: int = Query(30, ge=1, le=200),
    source_id: str | None = None,
    status: str | None = None,
) -> list[KnowledgeItem]:
    """Nearest items to the query by embedding similarity. Empty when
    semantic search is unavailable — the client falls back to FTS."""
    return await search.semantic_search(q, limit=limit, source_id=source_id, status=status)


# ---- Sync ----------------------------------------------------------------

@app.get(
    "/api/sync/history",
    response_model=list[SyncLogEntry],
    response_model_by_alias=True,
)
async def sync_history(limit: int = Query(10, ge=1, le=200)) -> list[SyncLogEntry]:
    return await store.list_sync_history(limit=limit)


@app.get(
    "/api/sync/jobs",
    response_model=list[SyncResult],
    response_model_by_alias=True,
)
async def sync_jobs(limit: int = Query(10, ge=1, le=100)) -> list[SyncResult]:
    """Recent sync runs (aggregated per job). The frontend polls this to
    notify on new items from background/scheduled syncs. Declared before the
    `/api/sync/{job_id}` route so `jobs` isn't captured as a job id."""
    return await store.list_recent_jobs(limit=limit)


@app.post(
    "/api/sync",
    response_model=SyncResult,
    response_model_by_alias=True,
)
async def trigger_sync() -> SyncResult:
    """Run the full pipeline over all enabled sources.

    v0.2b+: returns immediately with status=running; the pipeline runs
    as a background task. Poll GET /api/sync/{job_id} for the result.
    Concurrent calls return 409.
    """
    return await orchestrator.start_sync(source_id=None)


@app.post(
    "/api/sync/{source_id}",
    response_model=SyncResult,
    response_model_by_alias=True,
)
async def trigger_source_sync(source_id: str) -> SyncResult:
    """Run the pipeline for a single source."""
    return await orchestrator.start_sync(source_id=source_id)


@app.get(
    "/api/sync/{job_id}",
    response_model=SyncResult,
    response_model_by_alias=True,
)
async def get_sync_job(job_id: str) -> SyncResult:
    job = await store.get_job(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} not found")
    return job


# v0.2b+: cancel an in-flight sync. The endpoint is a POST (not
# DELETE) because cancellation is an action that mutates
# server-side state — the cancel flag stays set until the
# pipeline consumes it, even though the response is immediate.
# 404 if the job doesn't exist; 409 if it's already finished
# (cancelling a "done" job is a no-op the user shouldn't see
# as success — tell them why so they don't think the button
# is broken).
@app.post(
    "/api/sync/{job_id}/cancel",
    response_model_by_alias=True,
)
async def cancel_sync_job(job_id: str) -> dict:
    if job_id not in _inflight_jobs:
        # Either unknown or already done. Distinguish by hitting
        # the store so the user gets a useful error.
        job = await store.get_job(job_id)
        if not job:
            raise HTTPException(404, f"job {job_id} not found")
        raise HTTPException(
            409,
            f"job {job_id} is already {job.status.value}; nothing to cancel",
        )
    orchestrator.cancelled_jobs.add(job_id)
    log.info("[sync-cancel] user cancelled job=%s", job_id)
    return {"jobId": job_id, "cancelled": True}


# ---- Distill -------------------------------------------------------------

class RedistillResponse(BaseModel):
    started_pending: int
    distilled: int
    failed: int
    key_invalid: bool
    error: Optional[str] = None
    sample_failures: list[str] = []
    # v0.5.x: the batch runs as a background task; the POST returns
    # immediately with started_pending set and everything else zeroed.
    # Clients watch /api/distill/status (or the SSE stream) for the
    # live counters and the final outcome (lastError carries
    # "key_invalid: …" when the provider rejected the key mid-run).
    background: bool = False


@app.get("/api/distill/pending-count", response_model_by_alias=True)
async def pending_distill_count() -> dict:
    """How many items are waiting to be distilled. Cheap (count only)."""
    ids = await list_pending_distill_ids()
    return {"pending": len(ids)}


@app.get("/api/distill/status")
async def distill_status() -> dict:
    """One-shot snapshot of the current distill run.

    Returns the same shape as the SSE stream's per-event payload, so
    the frontend can use a single `DistillProgress` type for both
    the initial poll and the live updates. When no run is in flight
    the response is `{isRunning: false, ...}` — i.e. an "idle" state
    the UI can render as a hidden progress bar.
    """
    return progress_store.snapshot()


# How often the SSE handler emits a synthetic `keepalive` event when
# the pipeline is quiet. 15s is well under typical reverse-proxy /
# browser idle-connection timeouts and keeps the connection warm
# without flooding the wire.
_SSE_KEEPALIVE_SEC = 15.0
# How long the SSE handler waits for a real event before giving up
# and closing the stream. We don't actually want this to trip while
# a run is in flight (the pipeline can be slow on a long batch), so
# this is set high — 30 minutes — and the connection is closed
# normally by the `end_run` push when the run finishes.
_SSE_HARD_TIMEOUT_SEC = 30 * 60


@app.get("/api/distill/status/stream")
async def distill_status_stream():
    """Server-Sent Events stream of distill progress.

    The browser connects with a single ``EventSource`` and receives
    one ``data: {...JSON...}`` event per progress push (throttled to
    100ms by the store, so a 50-item batch over 30s yields ~300
    events, not 5000). Between real events we emit an SSE comment
    frame every 15s to keep the connection alive.

    Connection lifecycle: while no run is in flight the stream stays
    OPEN (kept warm by the keepalive comments) — it does NOT close on
    the initial idle snapshot. It closes only after a run that this
    consumer observed as running has ended (the ``isRunning=false``
    transition event is the last one), at which point the browser's
    EventSource reconnects and the cycle repeats. Closing on every
    idle snapshot (the pre-v0.2c behaviour) made the EventSource
    reconnect every ~3s for the whole app lifetime.

    Wire format
    -----------
    * Real updates: ``data: {"isRunning":true,"distilled":5,"pending":10,...}\\n\\n``
    * Keepalive:    ``: keepalive\\n\\n`` (SSE comment; ignored by EventSource)
    """
    queue = progress_store.subscribe()
    # Push the current snapshot immediately so a late-attaching
    # consumer (e.g. user opens the inbox mid-run) sees live state
    # without waiting for the next pipeline tick.
    try:
        initial = progress_store.snapshot()
        queue.put_nowait(initial)
    except Exception:  # pragma: no cover
        pass

    async def event_gen():
        import json as _json

        last_keepalive = time.monotonic()
        # Whether THIS consumer has seen the run in a running state.
        # We only close the stream on a running→idle transition; the
        # initial idle snapshot must NOT close it, otherwise an idle
        # app makes the EventSource reconnect every ~3s forever.
        had_running = False
        try:
            # Hard timeout safety: if a pipeline run hangs forever
            # (e.g. distiller deadlock), close the connection so
            # the browser sees a clean disconnect and can decide
            # whether to reconnect. For idle connections this just
            # means one clean reconnect every 30 minutes.
            deadline = time.monotonic() + _SSE_HARD_TIMEOUT_SEC
            while True:
                now = time.monotonic()
                if now >= deadline:
                    log.warning("[distill-status-sse] hard timeout; closing")
                    break
                if now - last_keepalive >= _SSE_KEEPALIVE_SEC:
                    yield ": keepalive\n\n"
                    last_keepalive = now
                try:
                    snap = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SEC)
                except asyncio.TimeoutError:
                    # Loop back; the keepalive check above will
                    # emit a comment if needed.
                    continue

                yield f"data: {_json.dumps(snap, ensure_ascii=False)}\n\n"
                if snap.get("isRunning", False):
                    had_running = True
                elif had_running:
                    # Running → idle transition: final event of this
                    # run — close so the client doesn't sit on a
                    # quiet connection; it reconnects for the next.
                    break
        finally:
            progress_store.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Disable nginx-style buffering: the browser should see
            # events as soon as we yield them. Without this some
            # proxies buffer the whole response until completion,
            # which defeats the purpose of SSE.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# Strong reference to the in-flight redistill background task, both to
# keep it from being GC'd mid-run and so the lifespan shutdown can
# cancel it before closing the DB.
_redistill_task: asyncio.Task | None = None


async def _redistill_background(batch_limit: int) -> None:
    """Run the redistill batch as a background task.

    Owns the progress_store end framing and the `redistill_running`
    flag; the route handler only does the begin framing (so the SSE
    consumers see the run the moment the POST returns).
    """
    error: Optional[str] = None
    try:
        result = await redistill_all_pending(batch_limit=batch_limit)
        error = result.error
        log.info(
            "[redistill-bg] finished: distilled=%d failed=%d key_invalid=%s",
            result.distilled, result.failed, result.key_invalid,
        )
    except asyncio.CancelledError:
        # Sidecar shutdown mid-batch. Each distilled item already
        # committed its own DB write and the progress store dies with
        # the process, so there is nothing to persist — just stop.
        orchestrator.redistill_running = False
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("[redistill-bg] batch crashed")
        error = f"redistill crashed: {exc!r}"
    orchestrator.redistill_running = False
    await progress_store.end_run(
        finished_at_iso=datetime.now(timezone.utc).isoformat(),
        error=error,
    )


@app.post("/api/distill/redistill", response_model=RedistillResponse, response_model_by_alias=True)
async def trigger_redistill(batch_limit: int = Query(1000, ge=1, le=5000)) -> RedistillResponse:
    """Start a background re-distill of every item with `distilled_at IS NULL`.

    Use cases:
      - The user just configured an API key for the first time and wants
        to back-fill the items that came in earlier without a key.
      - The user's key expired / ran out and they want a clean re-run
        after fixing it.

    v0.5.x: the batch runs as a BACKGROUND task — a 1000-item batch is
    hours of serial LLM calls, which no HTTP request should sit on. The
    response returns immediately with `background=true` and
    `started_pending` set; progress (including a mid-run key_invalid
    outcome) streams through /api/distill/status[/stream].
    """
    # v0.2a+: check the *active* provider (DeepSeek or MiniMax). The
    # legacy v0.1 `is_distiller_configured()` helper only checks
    # DEEPSEEK_API_KEY, which would falsely reject MiniMax users.
    active_provider = settings.load_active_provider()["provider"]
    if not settings.is_provider_configured(active_provider):
        raise HTTPException(
            503,
            f"distiller is not configured (set the API key in Settings for {active_provider})",
        )
    # Mutual exclusion with sync (and with a second redistill): both
    # drive the same progress_store and the same distiller quota, and
    # a concurrent begin_run/end_run pair would clobber the other
    # run's progress state (and prematurely close its SSE framing).
    if orchestrator.inflight_jobs or orchestrator.redistill_running:
        raise HTTPException(409, "a sync or redistill is already running")
    # Claim the slot IMMEDIATELY — same synchronous block as the check,
    # no await in between, so two concurrent requests can't both pass.
    orchestrator.redistill_running = True
    try:
        # We know the `pending` count up-front for redistill: it's the
        # number of items in the queue right now. Use that to drive a
        # proper determinate progress bar (X / pending) in the UI.
        pending_ids = await list_pending_distill_ids()
        capped_pending = min(len(pending_ids), batch_limit)
        await progress_store.begin_run(
            pending=capped_pending,
            started_at_iso=datetime.now(timezone.utc).isoformat(),
        )
    except BaseException:
        # Failed before the batch even started — release the slot,
        # close the progress framing we may have opened, and re-raise.
        orchestrator.redistill_running = False
        await progress_store.end_run(
            finished_at_iso=datetime.now(timezone.utc).isoformat(),
            error="redistill failed to start",
        )
        raise

    global _redistill_task
    _redistill_task = asyncio.create_task(_redistill_background(batch_limit))

    return RedistillResponse(
        started_pending=capped_pending,
        distilled=0,
        failed=0,
        key_invalid=False,
        error=None,
        sample_failures=[],
        background=True,
    )


# ---- Settings (LLM provider) --------------------------------------------


@app.get(
    "/api/settings/providers",
    response_model=list[settings.ProviderSchema],
    response_model_by_alias=True,
)
async def list_provider_schemas() -> list[settings.ProviderSchema]:
    """Static metadata describing each supported provider's Settings-UI shape.

    v0.2b pruned the provider list down to 2 (DeepSeek + MiniMax) — see
    `distillers/registry.py:PROVIDERS`. This docstring used to say "all 5
    providers"; kept generic now so it doesn't drift again if the count
    changes.

    The frontend uses this to decide which input fields to render
    after the user picks a provider from the dropdown.
    """
    return list(settings.PROVIDER_SCHEMAS)


@app.get(
    "/api/settings/llm",
    response_model=settings.LlmConfig,
    response_model_by_alias=True,
)
async def get_llm_config() -> settings.LlmConfig:
    """Current active LLM configuration (no API key returned)."""
    return settings.get_llm_status()


@app.post(
    "/api/settings/llm",
    response_model=settings.LlmConfig,
    response_model_by_alias=True,
)
async def set_llm_config(payload: settings.LlmConfigUpdate) -> settings.LlmConfig:
    """Switch the active LLM provider.

    The body MUST NOT include ``api_key`` — Tauri writes the key to the
    OS keychain and (re)launches the sidecar with the right env vars.
    If ``api_key`` is present in the body, we reject it with 400 so
    keys can never transit through the sidecar.

    Side effects on success:
      * rewrite ``active_provider.json``
      * best-effort hot-swap the cached distiller (a real change
        requires restarting the sidecar)
    """
    # Pydantic's exclude=True keeps api_key OUT of the serialized
    # response, but it still parses it on the way in. Read it back
    # off the model and reject explicitly — keys never transit HTTP.
    if payload.api_key not in (None, ""):
        raise HTTPException(
            400,
            "api_key not accepted via HTTP, use Tauri command",
        )

    provider = payload.provider
    if provider not in {s.id for s in settings.PROVIDER_SCHEMAS}:
        raise HTTPException(400, f"unknown provider: {provider!r}")

    try:
        settings.set_active_provider(
            provider,
            model=payload.model,
            base_url=payload.base_url,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Best-effort hot-swap. Pipeline builds a fresh distiller per job
    # anyway, so this only matters for any code path that reads the
    # cached one. We log and move on if it fails.
    global _current_distiller
    try:
        _current_distiller = _registry_get_distiller(
            provider, model=payload.model, base_url=payload.base_url,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "[prism-sidecar] hot-swap distiller failed (will rebuild next job): %s",
            exc,
        )

    return settings.get_llm_status()
