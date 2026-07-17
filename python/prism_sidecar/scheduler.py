"""APScheduler integration.

A single AsyncIOScheduler runs as a background task. The FastAPI lifespan
starts it on startup and shuts it down on exit.

The daily sync job is fire-and-forget at the configured hour in the
configured timezone. We don't return a job_id to anyone — it's a pure
side effect.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from prism_sidecar.config import DAILY_SYNC_ENABLED, DAILY_SYNC_HOUR, DAILY_SYNC_TZ

# Imported at module top since v0.5.x. The job entry points used to live
# in app.py, which imports this module — so this file reached them via a
# late `from prism_sidecar.app import ...` INSIDE each job coroutine to
# dodge the circular import at load time. The functions' real home is
# pipeline/orchestrator.py now (which never imports scheduler), so the
# cycle is gone and the dependency can sit where it's visible. The old
# shape also had the worst possible failure mode: an import problem
# surfaced not at startup but at the first cron fire the next morning.
from prism_sidecar.pipeline.orchestrator import (
    run_all_sync_background,
    run_failed_retry_background,
)

log = logging.getLogger(__name__)


_scheduler: Optional[AsyncIOScheduler] = None


def start_scheduler() -> AsyncIOScheduler:
    """Start the global scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone=DAILY_SYNC_TZ)
    # Both jobs MUST be coroutine functions (they are): APScheduler's
    # ``AsyncIOExecutor`` schedules coroutine jobs on the running loop,
    # but runs *sync* functions in a thread-pool worker via
    # ``run_in_executor``. A pre-v0.2b sync wrapper here called
    # ``asyncio.get_event_loop()`` from that worker thread, which raises
    # ``RuntimeError`` on Python 3.10+ (no event loop in a non-main
    # thread) — so the daily sync silently never ran.
    if DAILY_SYNC_ENABLED:
        _scheduler.add_job(
            run_all_sync_background,
            CronTrigger(hour=DAILY_SYNC_HOUR, minute=0, timezone=DAILY_SYNC_TZ),
            id="daily_sync",
            name=f"Daily sync at {DAILY_SYNC_HOUR:02d}:00 {DAILY_SYNC_TZ}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        # v0.2c: hourly failure-retry sweep. Runs at :30 so it never
        # collides with the daily job at :00; the orchestrator's lock
        # checks make an accidental overlap a harmless no-op anyway.
        _scheduler.add_job(
            run_failed_retry_background,
            CronTrigger(minute=30, timezone=DAILY_SYNC_TZ),
            id="failed_retry_sync",
            name="Hourly retry of failed sources (past cooldown)",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    _scheduler.start()
    log.info(
        "[prism-sidecar] scheduler started (daily_sync=%s)",
        "enabled" if DAILY_SYNC_ENABLED else "disabled",
    )
    return _scheduler


def shutdown_scheduler() -> None:
    """Stop the global scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("[scheduler] shutdown error: %s", exc)
        _scheduler = None


def get_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler


__all__ = ["start_scheduler", "shutdown_scheduler", "get_scheduler"]
