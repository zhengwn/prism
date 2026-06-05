"""APScheduler integration.

A single AsyncIOScheduler runs as a background task. The FastAPI lifespan
starts it on startup and shuts it down on exit.

The daily sync job is fire-and-forget at the configured hour in the
configured timezone. We don't return a job_id to anyone — it's a pure
side effect.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from prism_sidecar.config import DAILY_SYNC_ENABLED, DAILY_SYNC_HOUR, DAILY_SYNC_TZ

log = logging.getLogger(__name__)


_scheduler: Optional[AsyncIOScheduler] = None


def _safe_run_all_sync() -> None:
    """Wrap the coroutine so the scheduler can fire it from sync context."""
    from prism_sidecar.app import run_all_sync_background  # late import: avoid cycle

    try:
        asyncio.get_event_loop().create_task(run_all_sync_background())
    except RuntimeError:
        # No running loop (e.g. inside tests that tear down the loop). Skip.
        log.warning("[scheduler] no event loop; skipping scheduled sync")


def start_scheduler() -> AsyncIOScheduler:
    """Start the global scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone=DAILY_SYNC_TZ)
    if DAILY_SYNC_ENABLED:
        _scheduler.add_job(
            _safe_run_all_sync,
            CronTrigger(hour=DAILY_SYNC_HOUR, minute=0, timezone=DAILY_SYNC_TZ),
            id="daily_sync",
            name=f"Daily sync at {DAILY_SYNC_HOUR:02d}:00 {DAILY_SYNC_TZ}",
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
