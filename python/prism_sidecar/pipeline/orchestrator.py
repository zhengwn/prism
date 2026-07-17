"""Sync-job orchestration: concurrency control + job lifecycle.

This used to live inline in `app.py`, mixed in with the HTTP route
handlers — by v0.2c that file had grown to 900+ lines and it was hard
to tell "this is a FastAPI route" from "this is pipeline orchestration"
at a glance. This module owns the "how do we run / track / cancel a
sync job across one or more sources" logic; `app.py` now just calls
into it from thin route handlers, the same way it already delegated
distill-batch logic to `pipeline/distill.py`.

Backward compatibility
-----------------------
One external contract depended on the old location and is preserved
on purpose:

* `tests/test_api.py` reaches into `prism_sidecar.app._inflight_jobs`
  directly to assert on in-flight state. `app.py` re-exports
  `inflight_jobs` under that name (same `set` object, not a copy) so
  the test didn't need to change.

(`scheduler.py` used to be a second such contract — it did a late
`from prism_sidecar.app import run_all_sync_background` inside its job
coroutines to dodge the app↔scheduler import cycle. Since v0.5.x it
imports the job entry points from THIS module at load time; this module
never imports scheduler, so there is no cycle and app.py's forwarding
lines for the run_*_background pair are gone.)

Concurrency model (v0.5.x — fetch overlaps, writes stay serial)
----------------------------------------------------------------
`sync_lock` + `inflight_jobs` still guarantee at most one sync JOB
(manual or scheduled) at a time — two jobs would fight over the
progress_store framing and the distiller quota. WITHIN a job, the
per-source work is now pipelined:

* the network-bound FETCH stage runs for up to
  `config.SYNC_FETCH_CONCURRENCY` sources at once, so one slow
  YouTube/X/RSS source no longer blocks every other source's fetch
  (per-host politeness is the fetchers' HostThrottle, unchanged);
* the DB-WRITE + DISTILL stage is consumed strictly serially, in the
  original source order. Serial writes are a hard requirement, not a
  style choice: every store call shares ONE aiosqlite connection, and
  two tasks interleaving multi-statement write transactions would
  cross-commit each other's half-written rows — see the two-stage
  comment in `pipeline/sync.py`.

Results are consumed in source order (not completion order) to keep
job progress, sync_log rows, and tests deterministic; the trade-off is
head-of-line waiting on the progress READOUT only — the other fetches
keep running in the background regardless. Set
`PRISM_SYNC_FETCH_CONCURRENCY=1` to restore fully-serial behaviour.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from prism_sidecar import config, store
from prism_sidecar.models import Source, SyncJobStatus, SyncResult
from prism_sidecar.pipeline.sync import (
    FetchOutcome,
    fetch_source_data,
    process_fetched_source,
    source_in_cooldown,
    source_retry_due,
)
from prism_sidecar.progress import progress_store

log = logging.getLogger(__name__)


# ---- Concurrency control ------------------------------------------------

# We use an asyncio.Lock + a set of in-flight job IDs. The lock guarantees
# serialised access to the set; the set itself is the source of truth for
# "is anything running right now". The store-level `is_any_job_running` is
# a belt-and-suspenders check that also catches jobs created from the
# scheduler (or another process).
sync_lock: asyncio.Lock = asyncio.Lock()
inflight_jobs: set[str] = set()
# Job IDs the user has explicitly cancelled via
# POST /api/sync/{job_id}/cancel. The pipeline checks this set
# between sources; if it finds its own id here, it stops
# processing further sources (the current source's fetch is
# allowed to finish — the alternative is leaving a half-
# populated `items` row, which is uglier than a partial run).
#
# We keep the id in the set for the lifetime of the job so a
# second /cancel call doesn't re-trigger a "cancelled" toast;
# cleanup happens in the same try/finally that drops the job
# from `inflight_jobs`.
cancelled_jobs: set[str] = set()
is_app_ready: bool = False
# True while POST /api/distill/redistill is mid-batch. Sync and
# redistill share the progress_store (and the provider quota), so
# they exclude each other: start_sync refuses to start while this
# is set, and the redistill route refuses while inflight_jobs is
# non-empty. Set/cleared by the route handler in app.py.
redistill_running: bool = False

# Background tasks need a registry so a sidecar restart doesn't
# leave dangling "zombie" tasks in the asyncio runtime (they'd
# be GC'd eventually but it's tidier to track them by id).
inflight_tasks: dict[str, asyncio.Task] = {}


def is_job_cancelled(job_id: str) -> bool:
    """Cheap, lock-free check used by the pipeline between sources."""
    return job_id in cancelled_jobs


def consume_job_cancelled(job_id: str) -> bool:
    """Atomic check-and-clear — returns True exactly once per cancel.

    Used by the route handler to decide whether to surface a
    'cancelled' status in the SyncResult instead of 'done'. After
    this call, the cancel flag is gone (so a second consume returns
    False even though the user did cancel).
    """
    if job_id in cancelled_jobs:
        cancelled_jobs.discard(job_id)
        return True
    return False


def _has_inflight_job() -> bool:
    return len(inflight_jobs) > 0


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
    cancelled = False

    # Populated inside the try so the finally can always reference it,
    # even if an early stage raises before any task is created.
    fetch_tasks: dict[str, asyncio.Task] = {}
    fetch_sem = asyncio.Semaphore(max(1, int(config.SYNC_FETCH_CONCURRENCY)))

    async def _gated_fetch(src: Source) -> Optional[FetchOutcome]:
        async with fetch_sem:
            if is_job_cancelled(job_id):
                return None  # cancel won the race; never started
            return await fetch_source_data(src)

    try:
        # Stage 0: resolve the source rows up front (cheap serial reads)
        # so the fetch stage below launches for exactly the enabled
        # ones, in a known order.
        sources: list[tuple[str, Optional[Source]]] = []
        for sid in source_ids:
            sources.append((sid, await store.get_source(sid)))

        # Stage 1: launch the network fetches, at most
        # SYNC_FETCH_CONCURRENCY in flight at once (see the module
        # docstring for the model). The gate re-checks the cancel flag
        # so a cancel arriving mid-run stops queued fetches from ever
        # starting; fetches already in flight are cancelled in the
        # `finally` below — safe, because stage 1 performs no DB writes
        # (the old code let the in-flight source finish exactly because
        # fetch and write were one blob; they no longer are).
        for sid, src in sources:
            if src is not None and src.enabled:
                fetch_tasks[sid] = asyncio.create_task(_gated_fetch(src))

        # Stage 2: consume the fetch results IN THE ORIGINAL SOURCE
        # ORDER and run the DB-write + distill stage strictly serially.
        # In-order consumption (vs. as_completed) keeps job progress,
        # sync_log rows and tests deterministic; the other fetches keep
        # running in the background while the head is processed.
        for sid, source in sources:
            # v0.2b+: poll the cancel flag between sources — the write
            # stage is never interrupted mid-source, so a cancel still
            # lands on a clean per-source boundary with no half-written
            # rows.
            if is_job_cancelled(job_id):
                log.info("[sync] job=%s cancelled by user; stopping", job_id)
                cancelled = True
                break
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
                outcome = await fetch_tasks[sid]
                if outcome is None:
                    # The gated fetch observed the cancel flag before
                    # starting — same outcome as the loop-top check.
                    log.info("[sync] job=%s cancelled by user; stopping", job_id)
                    cancelled = True
                    break
                stats = await process_fetched_source(source, outcome)
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

        # Late cancel: the flag may have been set while the LAST
        # source was mid-fetch (the loop never got another chance
        # to poll it). Honour it here so the user who pressed
        # Cancel doesn't get a green "done" toast.
        if not cancelled and is_job_cancelled(job_id):
            cancelled = True

        # A user-cancelled run is a third outcome, distinct from
        # done / error. The frontend uses this to render "Sync
        # cancelled — X sources processed" instead of the green
        # "done" toast or the red "error" toast. We still write
        # the partial progress to the sync_jobs table so the
        # user can pick up where they left off next time.
        if cancelled:
            final_status = SyncJobStatus.cancelled
        elif first_error:
            final_status = SyncJobStatus.error
        else:
            final_status = SyncJobStatus.done
        # Eat the cancel flag now so the route handler can also
        # report cancelled=True (it consumes the same flag).
        consume_job_cancelled(job_id)
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
        # Never leak fetch tasks: on cancel, error, or normal return,
        # kill whatever is still in flight and wait for it to settle.
        # Safe to cancel mid-HTTP — the fetch stage writes nothing.
        pending = [t for t in fetch_tasks.values() if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Always close the live progress channel — happy path,
        # key-invalid early bail, and exception. The UI relies on
        # `is_running=false` to stop animating.
        await progress_store.end_run(
            finished_at_iso=datetime.now(timezone.utc).isoformat(),
            error=first_error,
        )


async def start_sync(source_id: Optional[str] = None) -> SyncResult:
    """Common entry point for /api/sync and /api/sync/{id}.

    v0.2a returned the final SyncResult synchronously — the
    client blocked until every source was fetched + distilled.
    v0.2b returns immediately with status=running, and the
    pipeline runs as a background task. The client polls
    /api/sync/{job_id} (or watches the SSE progress stream)
    to learn when the job finishes, and POSTs
    /api/sync/{job_id}/cancel to ask it to stop early.

    Returns the in-flight SyncResult placeholder. Raises
    HTTPException(409) if a sync is already running (unchanged from
    when this lived directly in `app.py` — the route handlers below
    just let it propagate, same as any other FastAPI route raising
    HTTPException).
    """
    # Optimistic check (no lock). The inflight set survives the actual
    # pipeline run, so this is the only check that can detect "another
    # sync is mid-flight" while the lock is free.
    if redistill_running:
        raise HTTPException(409, "a redistill batch is already running")
    if _has_inflight_job() or await store.is_any_job_running():
        raise HTTPException(409, "another sync is already running")

    # Lock just for the "create job + mark inflight" critical section.
    # We release the lock before spawning the background task so
    # other /api/sync requests can be served their 409 promptly.
    async with sync_lock:
        if redistill_running:
            raise HTTPException(409, "a redistill batch is already running")
        if _has_inflight_job() or await store.is_any_job_running():
            raise HTTPException(409, "another sync is already running")

        if source_id is not None:
            source = await store.get_source(source_id)
            if not source:
                raise HTTPException(404, f"source {source_id} not found")
            source_ids = [source_id]
            job_id = await store.create_job(source_id, sources_total=1)
        else:
            all_sources = await store.list_sources()
            source_ids = [s.id for s in all_sources if s.enabled]
            job_id = await store.create_job(None, sources_total=len(source_ids))

        inflight_jobs.add(job_id)
        started_at = datetime.now(timezone.utc)
        # Background task: runs the pipeline, drops the inflight
        # flag when finished. We DON'T await it here — the route
        # returns immediately so the user can interact with the
        # progress bar / cancel button.
        # Errors are swallowed by the task itself (it logs them
        # and the job row's error column gets the message), so we
        # don't need an explicit error handler here.
        inflight_tasks[job_id] = asyncio.create_task(
            _background_pipeline(
                source_ids=source_ids,
                job_id=job_id,
                job_source_id=source_id,
            )
        )

    # Return the in-flight placeholder. The client uses jobId
    # to poll /api/sync/{job_id} for the real result.
    return SyncResult(
        job_id=job_id,
        source_id=source_id,
        started_at=started_at,
        finished_at=None,
        status=SyncJobStatus.running,
        items_new=0,
        items_distilled=0,
        sources_total=len(source_ids),
        sources_done=0,
        error=None,
    )


async def _background_pipeline(
    *,
    source_ids: list[str],
    job_id: str,
    job_source_id: Optional[str],
) -> None:
    """The actual pipeline wrapper, run as a background task.

    Always cleans up `inflight_jobs` and `inflight_tasks` so a
    crash here doesn't leave the sidecar thinking it's busy
    forever. The body just calls the existing pipeline and
    discards its return value (the pipeline already writes the
    result to the sync_jobs row).
    """
    try:
        await _run_pipeline_for_sources(
            source_ids, job_id, job_source_id=job_source_id,
        )
    except Exception as exc:  # noqa: BLE001
        # The pipeline catches its own per-source exceptions, so
        # this branch only fires on truly unexpected errors (a
        # DB write that raises, a programming bug, etc.). Mark
        # the job as errored so the UI doesn't sit on "running"
        # forever.
        log.exception("[sync-bg] job=%s raised", job_id)
        try:
            await store.finish_job(
                job_id,
                status=SyncJobStatus.error,
                error=f"pipeline crashed: {exc!r}",
            )
        except Exception:  # pragma: no cover
            log.exception("[sync-bg] failed to mark job=%s as error", job_id)
    finally:
        inflight_jobs.discard(job_id)
        inflight_tasks.pop(job_id, None)
        # Normally consumed by the pipeline; this covers the crash
        # path so a cancel that raced an exception can't leak its
        # job id into the set forever.
        cancelled_jobs.discard(job_id)


async def run_all_sync_background() -> None:
    """Background entry point used by the scheduler.

    Failures are logged but never raised — the scheduler will retry on the
    next cron tick.
    """
    if not is_app_ready:
        log.warning("[scheduler] app not ready; skipping scheduled sync")
        return
    if redistill_running or _has_inflight_job() or await store.is_any_job_running():
        log.info("[scheduler] sync/redistill already running; skipping scheduled run")
        return
    try:
        async with sync_lock:
            if redistill_running or _has_inflight_job() or await store.is_any_job_running():
                return
            sources = await store.list_sources()
            # v0.2c: scheduled runs respect the failure cooldown — a
            # source that failed recently sits out until its window
            # expires (manual "Sync now" ignores this, see start_sync).
            source_ids = []
            for s in sources:
                if not s.enabled:
                    continue
                if await source_in_cooldown(s.id):
                    log.info("[scheduler] source %s in failure cooldown; skipping", s.id)
                    continue
                source_ids.append(s.id)
            if not source_ids:
                log.info("[scheduler] no enabled sources; nothing to do")
                return
            job_id = await store.create_job(None, sources_total=len(source_ids))
            inflight_jobs.add(job_id)
        # Pipeline runs without the lock held.
        try:
            log.info(
                "[scheduler] daily sync starting (job=%s, %d sources)",
                job_id, len(source_ids),
            )
            await _run_pipeline_for_sources(source_ids, job_id)
        finally:
            inflight_jobs.discard(job_id)
            cancelled_jobs.discard(job_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("[scheduler] daily sync failed: %s", exc)


async def run_failed_retry_background() -> None:
    """Hourly retry job (v0.2c): re-sync only known-failed sources
    whose cooldown has expired.

    Same mutual-exclusion rules as the daily job — if anything else is
    running we simply skip this tick and let the next one try.
    """
    if not is_app_ready:
        return
    if redistill_running or _has_inflight_job() or await store.is_any_job_running():
        log.debug("[scheduler] retry tick: busy, skipping")
        return
    try:
        async with sync_lock:
            if redistill_running or _has_inflight_job() or await store.is_any_job_running():
                return
            sources = await store.list_sources()
            due_ids = []
            for s in sources:
                if s.enabled and await source_retry_due(s.id):
                    due_ids.append(s.id)
            if not due_ids:
                return
            job_id = await store.create_job(None, sources_total=len(due_ids))
            inflight_jobs.add(job_id)
        try:
            log.info(
                "[scheduler] failure-retry sync starting (job=%s, %d sources)",
                job_id, len(due_ids),
            )
            await _run_pipeline_for_sources(due_ids, job_id)
        finally:
            inflight_jobs.discard(job_id)
            cancelled_jobs.discard(job_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("[scheduler] failure-retry sync failed: %s", exc)


async def drain_inflight(grace_sec: float = 4.0) -> None:
    """Graceful-shutdown helper (v0.2c): ask every in-flight sync job to
    stop at its next per-source checkpoint, then wait up to `grace_sec`
    for the background tasks to finish.

    Called from the FastAPI lifespan shutdown (i.e. after uvicorn got
    SIGTERM). The cancel-flag mechanism is the same one the user's
    Cancel button uses, so a drained job lands in status=cancelled with
    its partial progress written — not an orphaned 'running' row that
    `fail_orphan_running_jobs` has to mop up on next boot.

    Waiting "forever" is deliberately NOT offered: the Tauri side only
    gives the process a few seconds before SIGKILL, and a distill run
    can take minutes. Stopping at the source boundary + persisting
    partial progress IS the graceful outcome.
    """
    # Flag every known job — both task-tracked (manual/API) and
    # scheduler-started (which only registers in inflight_jobs).
    all_job_ids = set(inflight_jobs) | set(inflight_tasks.keys())
    if not all_job_ids:
        return
    log.info(
        "[shutdown] requesting stop for %d in-flight sync job(s), grace=%.1fs",
        len(all_job_ids), grace_sec,
    )
    for job_id in all_job_ids:
        cancelled_jobs.add(job_id)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + grace_sec

    tasks = [t for t in inflight_tasks.values() if not t.done()]
    if tasks:
        _done, pending = await asyncio.wait(
            tasks, timeout=max(0.0, deadline - loop.time()),
        )
    else:
        pending = set()

    # Scheduler-started pipelines aren't in inflight_tasks — poll the
    # job registry for the remainder of the grace window.
    while inflight_jobs and loop.time() < deadline:
        await asyncio.sleep(0.1)

    if pending or inflight_jobs:
        log.warning(
            "[shutdown] %d task(s) / %d job(s) still running after grace; "
            "cancelling hard (job rows were already flagged cancelled)",
            len(pending), len(inflight_jobs),
        )
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    else:
        log.info("[shutdown] all in-flight sync work drained cleanly")


__all__ = [
    "sync_lock",
    "inflight_jobs",
    "cancelled_jobs",
    "inflight_tasks",
    "is_app_ready",
    "redistill_running",
    "is_job_cancelled",
    "consume_job_cancelled",
    "start_sync",
    "run_all_sync_background",
    "run_failed_retry_background",
    "drain_inflight",
]
