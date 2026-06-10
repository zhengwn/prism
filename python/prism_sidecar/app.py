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
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from prism_sidecar import __version__, scheduler, settings, store
from prism_sidecar.progress import progress_store
from prism_sidecar.config import (
    DAILY_SYNC_ENABLED,
    DAILY_SYNC_HOUR,
    DAILY_SYNC_TZ,
    PRISM_DB_PATH,
)
from prism_sidecar.data.fixtures import SEED_SOURCES
from prism_sidecar.db import close_db, init_db
from prism_sidecar.distillers.registry import get_distiller as _registry_get_distiller
from prism_sidecar.models import (
    HealthInfo,
    ItemStatus,
    KnowledgeItem,
    Source,
    SourceCreate,
    SourcePatch,
    SyncJobStatus,
    SyncLogEntry,
    SyncResult,
)
from prism_sidecar.pipeline.distill import list_pending_distill_ids, redistill_all_pending
from prism_sidecar.pipeline.sync import run_source_sync

# ---- Logging -------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("prism-sidecar")


# ---- Concurrency control ------------------------------------------------

# We use an asyncio.Lock + a set of in-flight job IDs. The lock guarantees
# serialised access to the set; the set itself is the source of truth for
# "is anything running right now". The store-level `is_any_job_running` is
# a belt-and-suspenders check that also catches jobs created from the
# scheduler (or another process).
_sync_lock: asyncio.Lock = asyncio.Lock()
_inflight_jobs: set[str] = set()
_is_app_ready: bool = False

# Cached reference to the most recently built distiller. The pipeline
# builds a fresh distiller per job (so config changes between jobs are
# picked up), but the /api/settings/llm endpoint can hot-swap this
# reference for callers that want to test the change immediately.
#
# TODO(v0.2a): this is best-effort. The reliable way to apply a
# provider change is to restart the sidecar (Tauri kills + respawns
# it). Hot-swap is documented as a "may help for quick UI feedback"
# nicety, not a guarantee.
_current_distiller: object | None = None


def _has_inflight_job() -> bool:
    return len(_inflight_jobs) > 0


# ---- Pipeline helpers ---------------------------------------------------

async def _run_pipeline_for_sources(
    source_ids: list[str],
    job_id: str,
    *,
    job_source_id: Optional[str] = None,
) -> SyncResult:
    """Run sync over a list of source ids, updating the job row as we go."""
    items_new = 0
    items_distilled = 0
    sources_done = 0
    sources_total = len(source_ids)
    first_error: Optional[str] = None
    started_at = datetime.now(timezone.utc)

    # v0.2a+: open the live progress channel so the inbox can show a
    # "distilling N items" bar while the pipeline is grinding. We
    # don't know the exact `pending` count up front (it depends on
    # how many NEW items each source yields), so we open with 0 and
    # let the UI treat the running state as "indeterminate" — once
    # the run ends the totals tell the truth.
    await progress_store.begin_run(
        pending=0,
        started_at_iso=started_at.isoformat(),
    )
    try:
        for sid in source_ids:
            source = await store.get_source(sid)
            if source is None:
                log.warning("[sync] job=%s source %s not found, skipping", job_id, sid)
                continue
            if not source.enabled:
                log.info("[sync] job=%s source %s disabled, skipping", job_id, sid)
                sources_done += 1
                await store.update_job_progress(
                    job_id,
                    items_new=items_new,
                    items_distilled=items_distilled,
                    sources_done=sources_done,
                )
                await store.write_sync_log(
                    source_id=sid,
                    job_id=job_id,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items_new=0,
                    items_distilled=0,
                    error="disabled",
                )
                continue

            try:
                log_src_started = datetime.now(timezone.utc).isoformat()
                stats = await run_source_sync(source)
                sources_done += 1
                items_new += stats.new_items
                items_distilled += stats.distilled
                err = stats.error
                if err and first_error is None:
                    first_error = f"{source.name}: {err}"
                await store.update_job_progress(
                    job_id,
                    items_new=items_new,
                    items_distilled=items_distilled,
                    sources_done=sources_done,
                )
                await store.write_sync_log(
                    source_id=sid,
                    job_id=job_id,
                    started_at=log_src_started,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items_new=stats.new_items,
                    items_distilled=stats.distilled,
                    error=err,
                )
            except Exception as exc:  # noqa: BLE001
                sources_done += 1
                log.exception("[sync] job=%s source %s raised", job_id, sid)
                if first_error is None:
                    first_error = f"{source.name}: {exc!r}"
                await store.update_job_progress(
                    job_id,
                    items_new=items_new,
                    items_distilled=items_distilled,
                    sources_done=sources_done,
                )
                await store.write_sync_log(
                    source_id=sid,
                    job_id=job_id,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items_new=0,
                    items_distilled=0,
                    error=str(exc),
                )

        final_status = SyncJobStatus.error if first_error else SyncJobStatus.done
        await store.finish_job(
            job_id,
            status=final_status,
            items_new=items_new,
            items_distilled=items_distilled,
            sources_total=sources_total,
            sources_done=sources_done,
            error=first_error,
        )
        return SyncResult(
            job_id=job_id,
            source_id=job_source_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            status=final_status,
            items_new=items_new,
            items_distilled=items_distilled,
            sources_total=sources_total,
            sources_done=sources_done,
            error=first_error,
        )
    finally:
        # Always close the live progress channel — happy path,
        # key-invalid early bail, and exception. The UI relies on
        # `is_running=false` to stop animating.
        await progress_store.end_run(
            finished_at_iso=datetime.now(timezone.utc).isoformat(),
            error=first_error,
        )


async def _start_sync(source_id: Optional[str] = None) -> SyncResult:
    """Common entry point for /api/sync and /api/sync/{id}.

    Returns the final SyncResult. Raises HTTPException(409) if a sync is
    already running.
    """
    # Optimistic check (no lock). The inflight set survives the actual
    # pipeline run, so this is the only check that can detect "another
    # sync is mid-flight" while the lock is free.
    if _has_inflight_job() or await store.is_any_job_running():
        raise HTTPException(409, "another sync is already running")

    # Lock just for the "create job + mark inflight" critical section.
    # We release the lock before running the actual pipeline so other
    # /api/sync requests can be served their 409 promptly.
    async with _sync_lock:
        if _has_inflight_job() or await store.is_any_job_running():
            raise HTTPException(409, "another sync is already running")

        if source_id is not None:
            source = await store.get_source(source_id)
            if not source:
                raise HTTPException(404, f"source {source_id} not found")
            source_ids = [source_id]
            job_id = await store.create_job(source_id)
        else:
            all_sources = await store.list_sources()
            source_ids = [s.id for s in all_sources if s.enabled]
            job_id = await store.create_job(None)

        _inflight_jobs.add(job_id)

    # Pipeline runs WITHOUT the lock. The inflight set is the gate.
    try:
        result = await _run_pipeline_for_sources(
            source_ids, job_id, job_source_id=source_id,
        )
    finally:
        _inflight_jobs.discard(job_id)
    return result


async def run_all_sync_background() -> None:
    """Background entry point used by the scheduler.

    Failures are logged but never raised — the scheduler will retry on the
    next cron tick.
    """
    if not _is_app_ready:
        log.warning("[scheduler] app not ready; skipping scheduled sync")
        return
    if _has_inflight_job() or await store.is_any_job_running():
        log.info("[scheduler] sync already running; skipping scheduled run")
        return
    try:
        async with _sync_lock:
            if _has_inflight_job() or await store.is_any_job_running():
                return
            sources = await store.list_sources()
            source_ids = [s.id for s in sources if s.enabled]
            if not source_ids:
                log.info("[scheduler] no enabled sources; nothing to do")
                return
            job_id = await store.create_job(None)
            _inflight_jobs.add(job_id)
        # Pipeline runs without the lock held.
        try:
            log.info(
                "[scheduler] daily sync starting (job=%s, %d sources)",
                job_id, len(source_ids),
            )
            await _run_pipeline_for_sources(source_ids, job_id)
        finally:
            _inflight_jobs.discard(job_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("[scheduler] daily sync failed: %s", exc)


# ---- Lifespan ------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_app_ready
    log.info("[prism-sidecar] v%s starting up", __version__)
    log.info("[prism-sidecar] db: %s", PRISM_DB_PATH)

    # Read the active provider config (or create the default file).
    active = settings.write_default_if_missing()
    log.info(
        "[prism-sidecar] active LLM provider: %s (model=%s, base_url=%s)",
        active["provider"], active.get("model"), active.get("base_url"),
    )
    log.info(
        "[prism-sidecar] distiller: %s",
        "configured" if settings.is_provider_configured(active["provider"])
        else f"NOT configured ({active['provider']} env key missing)",
    )

    await init_db()
    await store.ensure_default_sources(SEED_SOURCES)

    if DAILY_SYNC_ENABLED:
        scheduler.start_scheduler()
    else:
        log.info("[prism-sidecar] daily sync disabled via env")

    _is_app_ready = True
    log.info(
        "[prism-sidecar] ready on http://127.0.0.1:8765 (daily_sync=%02d:00 %s)",
        DAILY_SYNC_HOUR, DAILY_SYNC_TZ,
    )

    try:
        yield
    finally:
        _is_app_ready = False
        log.info("[prism-sidecar] shutting down")
        scheduler.shutdown_scheduler()
        await close_db()


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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[KnowledgeItem]:
    return await store.list_items(
        source_id=source_id, status=status, q=q, limit=limit, offset=offset,
    )


@app.get("/api/items/{item_id}", response_model=KnowledgeItem, response_model_by_alias=True)
async def get_item(item_id: str) -> KnowledgeItem:
    it = await store.get_item(item_id)
    if not it:
        raise HTTPException(404, f"item {item_id} not found")
    return it


# ---- Sync ----------------------------------------------------------------

@app.get(
    "/api/sync/history",
    response_model=list[SyncLogEntry],
    response_model_by_alias=True,
)
async def sync_history(limit: int = Query(10, ge=1, le=200)) -> list[SyncLogEntry]:
    return await store.list_sync_history(limit=limit)


@app.post(
    "/api/sync",
    response_model=SyncResult,
    response_model_by_alias=True,
)
async def trigger_sync() -> SyncResult:
    """Run the full pipeline over all enabled sources.

    Synchronous: returns only after the job is finished. Concurrent calls
    return 409.
    """
    return await _start_sync(source_id=None)


@app.post(
    "/api/sync/{source_id}",
    response_model=SyncResult,
    response_model_by_alias=True,
)
async def trigger_source_sync(source_id: str) -> SyncResult:
    """Run the pipeline for a single source."""
    return await _start_sync(source_id=source_id)


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


# ---- Distill -------------------------------------------------------------

class RedistillResponse(BaseModel):
    started_pending: int
    distilled: int
    failed: int
    key_invalid: bool
    error: Optional[str] = None
    sample_failures: list[str] = []


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

    The stream ends when the current run ends (``isRunning=false``
    is the last event); the EventSource auto-reconnects but the
    client should check the flag and close the connection on its
    end so it doesn't keep reconnecting forever.

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
        last_keepalive = time.monotonic()
        try:
            # Hard timeout safety: if a pipeline run hangs forever
            # (e.g. distiller deadlock), close the connection so
            # the browser sees a clean disconnect and can decide
            # whether to reconnect.
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
                import json as _json

                yield f"data: {_json.dumps(snap, ensure_ascii=False)}\n\n"
                # If the run is done, this is the final event —
                # close the stream so the client doesn't sit on
                # a quiet connection.
                if not snap.get("isRunning", False):
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


@app.post("/api/distill/redistill", response_model=RedistillResponse, response_model_by_alias=True)
async def trigger_redistill(batch_limit: int = Query(1000, ge=1, le=5000)) -> RedistillResponse:
    """Re-run distillation on every item that still has `distilled_at IS NULL`.

    Use cases:
      - The user just configured an API key for the first time and wants
        to back-fill the items that came in earlier without a key.
      - The user's key expired / ran out and they want a clean re-run
        after fixing it.

    If the configured key is invalid, the response will have
    `key_invalid: true` and we'll stop the batch early so we don't
    burn credit on a dead key.
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
    # We know the `pending` count up-front for redistill: it's the
    # number of items in the queue right now. Use that to drive a
    # proper determinate progress bar (X / pending) in the UI.
    from prism_sidecar.pipeline.distill import list_pending_distill_ids

    pending_ids = await list_pending_distill_ids()
    capped_pending = min(len(pending_ids), batch_limit)
    await progress_store.begin_run(
        pending=capped_pending,
        started_at_iso=datetime.now(timezone.utc).isoformat(),
    )
    result = None
    try:
        result = await redistill_all_pending(batch_limit=batch_limit)
    finally:
        await progress_store.end_run(
            finished_at_iso=datetime.now(timezone.utc).isoformat(),
            error=result.error if result is not None else None,
        )
    return RedistillResponse(
        started_pending=result.started_pending,
        distilled=result.distilled,
        failed=result.failed,
        key_invalid=result.key_invalid,
        error=result.error,
        sample_failures=result.sample_failures,
    )


# ---- Settings (LLM provider) --------------------------------------------


@app.get(
    "/api/settings/providers",
    response_model=list[settings.ProviderSchema],
    response_model_by_alias=True,
)
async def list_provider_schemas() -> list[settings.ProviderSchema]:
    """Static metadata describing all 5 providers' Settings-UI shape.

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
