"""Distill progress tracker — in-memory singleton shared across the
sidecar process.

Why a module-level singleton (not a Redis / SQLite row)
-------------------------------------------------------
* The progress is **transient, not durable**: we only need to render
  a "正在蒸馏 X / Y" indicator on the inbox. Losing it on sidecar
  restart is fine — the user just sees a brief "indeterminate" state
  and then nothing.
* Per-pipeline work is serialised by the asyncio lock in
  ``pipeline.orchestrator.sync_lock`` / ``.inflight_jobs``, so a single
  in-process counter is always consistent.
* Multiple concurrent readers (the SSE stream consumers) just need
  a consistent snapshot; we serve each consumer from the latest
  ``Dict`` on every event.

What we track
-------------
* ``pending``      — total items the current run will touch (set at
                     run-start; stays put until the run ends).
* ``distilled``    — items successfully distilled.
* ``failed``       — items that raised (or returned unparseable output
                     after all retries). Distinct from ``distilled``
                     so the UI can show "23 ok / 1 failed" honestly.
* ``current_title``— the article title of the item currently being
                     distilled. Shown in the progress UI so the user
                     sees what's actually being worked on.
* ``current_source``— source name; ditto.
* ``is_running``   — True between ``begin_run`` and ``end_run``. Drives
                     the SSE stream's "open/close" framing.
* ``started_at`` / ``finished_at`` — ISO-8601 UTC. Used to render
                     "已运行 12s" and to drop stale progress after a
                     long quiet period.
* ``last_event_at`` — wall-clock of the most recent state mutation. We
                     snapshot this on every update so the SSE stream
                     can throttle outgoing events (don't spam 100/s).
* ``subscribers``  — set of ``asyncio.Queue``s that get a copy of the
                     state on every update. The SSE handler reads
                     from its own queue and forwards to the wire.

Concurrency model
-----------------
* All public methods are coroutines and grab ``_lock`` while mutating.
* The lock is held for microseconds (dict copy), so a stalled SSE
  consumer can't block the pipeline.
* ``snapshot()`` returns a fresh shallow copy; the dict it returns is
  safe to use outside the lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional


log = logging.getLogger(__name__)


@dataclass(slots=True)
class DistillProgress:
    """The public, JSON-serialisable progress snapshot."""

    is_running: bool = False
    pending: int = 0
    distilled: int = 0
    failed: int = 0
    current_title: Optional[str] = None
    current_source: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_event_at: float = 0.0
    # When a run ends in `error`, this carries the message (e.g.
    # "key_invalid" or "distiller_not_configured") so the UI can show
    # a useful toast.
    last_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "isRunning": self.is_running,
            "pending": self.pending,
            "distilled": self.distilled,
            "failed": self.failed,
            "currentTitle": self.current_title,
            "currentSource": self.current_source,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "lastEventAt": self.last_event_at,
            "lastError": self.last_error,
        }


class DistillProgressStore:
    """The singleton. Lives at module scope; one per sidecar process.

    Use ``begin_run(pending)`` once at the start of a sync / redistill
    job, then ``item_started(...)`` / ``item_succeeded(...)`` /
    ``item_failed(...)`` as the pipeline ticks through. ``end_run()``
    marks the run done and pushes a final event so any open SSE
    consumer closes its connection cleanly.
    """

    # Minimum gap between SSE pushes. 100ms is fast enough to feel
    # live but cheap enough that a 50-item batch over 30s emits ~300
    # events, not 5000. We snapshot on every internal mutation but
    # only push to subscribers at this throttle.
    PUSH_THROTTLE_SEC = 0.1

    def __init__(self) -> None:
        self._state = DistillProgress()
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._last_push_monotonic = 0.0

    # --- read API ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return the current state as a JSON-ready dict. Cheap, no lock.

        The dict is a fresh copy; the caller can hand it to the wire
        or to the UI without further coordination.
        """
        return self._state.to_dict()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a new SSE consumer. Returns a queue that will
        receive ``snapshot()`` dicts until ``unsubscribe`` is called.

        The queue is unbounded; the SSE handler drains it per-event
        and the only consumer is the sidecar's own stream endpoint, so
        backpressure isn't a concern in practice.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    # --- write API --------------------------------------------------------

    async def begin_run(
        self,
        pending: int,
        *,
        started_at_iso: str,
    ) -> None:
        """Reset counters and mark the run as started. The very first
        call also pushes a "started" event to any subscribers, so
        late-attaching SSE consumers see the run as live (and any
        consumers that were watching a previous finished run can
        refresh their state)."""
        async with self._lock:
            self._state = DistillProgress(
                is_running=True,
                pending=max(0, pending),
                distilled=0,
                failed=0,
                current_title=None,
                current_source=None,
                started_at=started_at_iso,
                finished_at=None,
                last_event_at=time.time(),
                last_error=None,
            )
        await self._push(force=True)

    async def item_started(
        self, *, title: Optional[str], source: Optional[str],
    ) -> None:
        async with self._lock:
            self._state.current_title = title
            self._state.current_source = source
            self._state.last_event_at = time.time()
        await self._push()

    async def item_succeeded(self) -> None:
        async with self._lock:
            self._state.distilled += 1
            self._state.current_title = None
            self._state.current_source = None
            self._state.last_event_at = time.time()
        await self._push()

    async def item_failed(self) -> None:
        async with self._lock:
            self._state.failed += 1
            self._state.current_title = None
            self._state.current_source = None
            self._state.last_event_at = time.time()
        await self._push()

    async def end_run(
        self,
        *,
        finished_at_iso: str,
        error: Optional[str] = None,
    ) -> None:
        async with self._lock:
            self._state.is_running = False
            self._state.finished_at = finished_at_iso
            self._state.last_error = error
            self._state.current_title = None
            self._state.current_source = None
            self._state.last_event_at = time.time()
        # Always force the final push — the SSE consumer may have
        # missed the last throttled event and the connection-closing
        # "run done" event is what the UI uses to flip back to idle.
        await self._push(force=True)

    # --- internals --------------------------------------------------------

    async def _push(self, *, force: bool = False) -> None:
        """Broadcast the current snapshot to all subscribers.

        We throttle to ``PUSH_THROTTLE_SEC`` to avoid waking a 30s
        SSE connection on every per-item update. ``force=True``
        bypasses the throttle (used at run start/end).
        """
        now = time.monotonic()
        if not force and (now - self._last_push_monotonic) < self.PUSH_THROTTLE_SEC:
            return
        self._last_push_monotonic = now

        snap = self.snapshot()
        # Iterate over a copy so an unsubscribe during iteration is safe.
        for q in list(self._subscribers):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:  # pragma: no cover — queues are unbounded
                log.warning("[progress] subscriber queue full; dropping event")


# Module-level singleton — every importer shares the same instance,
# which is exactly what we want (one progress view per sidecar).
progress_store = DistillProgressStore()


__all__ = [
    "DistillProgress",
    "DistillProgressStore",
    "progress_store",
]
