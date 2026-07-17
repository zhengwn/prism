"""Sync jobs (aggregated runs) + per-source sync_log history."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from prism_sidecar.db import get_db
from prism_sidecar.models import SyncJobStatus, SyncLogEntry, SyncResult
from prism_sidecar.store._shared import _new_id, _parse_iso

log = logging.getLogger(__name__)


async def create_job(source_id: Optional[str], *, sources_total: int = 0) -> str:
    """Create a sync_jobs row and return the job_id.

    ``sources_total`` is written at creation time so a client polling
    GET /api/sync/{job_id} mid-run sees "done/total" instead of "0/0".
    Every caller knows its source list before creating the job; the
    column used to be written only by `finish_job`, which made the
    running-state response lie until the job ended.
    """
    db = get_db()
    job_id = _new_id("job")
    await db.execute(
        """
        INSERT INTO sync_jobs (job_id, source_id, status, started_at, sources_total)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (job_id, source_id, datetime.now(timezone.utc).isoformat(), sources_total),
    )
    await db.commit()
    return job_id


async def finish_job(
    job_id: str,
    *,
    status: SyncJobStatus,
    items_new: int = 0,
    items_distilled: int = 0,
    sources_total: int = 0,
    sources_done: int = 0,
    error: Optional[str] = None,
) -> None:
    db = get_db()
    await db.execute(
        """
        UPDATE sync_jobs
        SET status = ?, finished_at = ?, items_new = ?, items_distilled = ?,
            sources_total = ?, sources_done = ?, error = ?
        WHERE job_id = ?
        """,
        (
            status.value,
            datetime.now(timezone.utc).isoformat(),
            items_new,
            items_distilled,
            sources_total,
            sources_done,
            error,
            job_id,
        ),
    )
    await db.commit()


async def update_job_progress(
    job_id: str,
    *,
    items_new: int,
    items_distilled: int,
    sources_done: int,
) -> None:
    """Update a job's running counters."""
    db = get_db()
    await db.execute(
        """
        UPDATE sync_jobs
        SET items_new = ?, items_distilled = ?, sources_done = ?
        WHERE job_id = ?
        """,
        (items_new, items_distilled, sources_done, job_id),
    )
    await db.commit()


async def get_job(job_id: str) -> Optional[SyncResult]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT job_id, source_id, status, started_at, finished_at,
               items_new, items_distilled, sources_total, sources_done, error
        FROM sync_jobs WHERE job_id = ?
        """,
        (job_id,),
    )
    row = await cur.fetchone()
    if not row:
        return None
    (
        jid, sid, status, started, finished,
        new_n, distilled_n, total, done, err,
    ) = row
    return SyncResult(
        job_id=jid,
        source_id=sid,  # type: ignore[arg-type]
        started_at=_parse_iso(started) or datetime.now(timezone.utc),
        finished_at=_parse_iso(finished),
        status=SyncJobStatus(status),
        items_new=int(new_n or 0),
        items_distilled=int(distilled_n or 0),
        sources_total=int(total or 0),
        sources_done=int(done or 0),
        error=err,
    )


async def list_recent_jobs(limit: int = 10) -> list[SyncResult]:
    """Recent sync JOBS (aggregated per run), newest first.

    Distinct from `list_sync_history`, which returns per-source `sync_log`
    rows — a job row carries the run-level `items_new` total, which is what
    the frontend's new-item notifications key off (one notification per run,
    not one per source).
    """
    db = get_db()
    cur = await db.execute(
        """
        SELECT job_id, source_id, status, started_at, finished_at,
               items_new, items_distilled, sources_total, sources_done, error
        FROM sync_jobs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = await cur.fetchall()
    out: list[SyncResult] = []
    for (jid, sid, status, started, finished, new_n, distilled_n, total, done, err) in rows:
        out.append(
            SyncResult(
                job_id=jid,
                source_id=sid,  # type: ignore[arg-type]
                started_at=_parse_iso(started) or datetime.now(timezone.utc),
                finished_at=_parse_iso(finished),
                status=SyncJobStatus(status),
                items_new=int(new_n or 0),
                items_distilled=int(distilled_n or 0),
                sources_total=int(total or 0),
                sources_done=int(done or 0),
                error=err,
            )
        )
    return out


async def is_any_job_running() -> bool:
    """True if any sync_jobs row is currently in 'running' state."""
    db = get_db()
    cur = await db.execute(
        "SELECT 1 FROM sync_jobs WHERE status = 'running' LIMIT 1"
    )
    return (await cur.fetchone()) is not None


async def fail_orphan_running_jobs() -> int:
    """Mark leftover 'running' jobs as errored. Returns the count fixed.

    Called once at startup (lifespan). If the sidecar crashed or was
    killed mid-sync, its job row stays 'running' forever — and because
    `is_any_job_running()` treats that row as an active sync, every
    subsequent /api/sync would 409 until the DB was hand-edited. No
    such job can actually be running at startup (jobs live only in
    this process), so flipping them to 'error' is always safe.
    """
    db = get_db()
    cur = await db.execute(
        """
        UPDATE sync_jobs
        SET status = 'error', finished_at = ?,
            error = 'orphaned: sidecar exited mid-run'
        WHERE status = 'running'
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    await db.commit()
    if cur.rowcount:
        log.warning(
            "[store] marked %d orphaned running sync job(s) as error", cur.rowcount
        )
    return cur.rowcount


async def write_sync_log(
    *,
    source_id: Optional[str],
    job_id: Optional[str],
    started_at: str,
    finished_at: Optional[str],
    items_new: int,
    items_distilled: int,
    error: Optional[str],
) -> None:
    db = get_db()
    await db.execute(
        """
        INSERT INTO sync_log
            (source_id, job_id, started_at, finished_at, items_new, items_distilled, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, job_id, started_at, finished_at, items_new, items_distilled, error),
    )
    await db.commit()


async def list_sync_history(limit: int = 10) -> list[SyncLogEntry]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT id, source_id, started_at, finished_at, items_new, items_distilled, error
        FROM sync_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = await cur.fetchall()
    out: list[SyncLogEntry] = []
    for row in rows:
        (log_id, sid, started, finished, new_n, distilled_n, err) = row
        out.append(
            SyncLogEntry(
                id=int(log_id),
                source_id=sid,
                started_at=_parse_iso(started) or datetime.now(timezone.utc),
                finished_at=_parse_iso(finished),
                items_new=int(new_n or 0),
                items_distilled=int(distilled_n or 0),
                error=err,
            )
        )
    return out
