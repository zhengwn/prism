"""Concurrency behaviour of the v0.5.x two-stage sync pipeline.

Targets `orchestrator._run_pipeline_for_sources` directly (real tmp-dir
DB from conftest, fake fetchers/distillers) and pins down the contract
introduced by the fetch-parallelisation:

* fetches for different sources OVERLAP (up to SYNC_FETCH_CONCURRENCY);
* the DB-write + distill stage never runs two sources at once, and
  consumes results in the original source order;
* PRISM_SYNC_FETCH_CONCURRENCY=1 restores the old fully-serial fetch;
* cancel mid-run still lands status=cancelled on a per-source boundary.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from prism_sidecar import store
from prism_sidecar.db import init_db
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers import registry
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType, SyncJobStatus
from prism_sidecar.pipeline import orchestrator
from prism_sidecar.pipeline import sync as sync_mod


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url,
        title=title,
        content=f"body {title}",
        published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


class Gauge:
    """Records concurrent entries into a stage + the entry order."""

    def __init__(self) -> None:
        self.active = 0
        self.high_water = 0
        self.order: list[str] = []

    def enter(self, label: str) -> None:
        self.active += 1
        self.high_water = max(self.high_water, self.active)
        self.order.append(label)

    def exit(self) -> None:
        self.active -= 1


class GaugedFetcher:
    """Fake fetcher that reports its concurrency through a shared Gauge.

    The `asyncio.sleep` is essential: it yields to the event loop so
    other fetch tasks get a chance to enter while this one is "on the
    network" — that's what makes the high-water mark meaningful.
    """

    def __init__(self, gauge: Gauge, items: list[RawItem], delay: float = 0.02):
        self.gauge = gauge
        self.items = items
        self.delay = delay

    async def fetch(self, source, *, lookback_days: int | None = None) -> list[RawItem]:
        self.gauge.enter(source.id)
        try:
            await asyncio.sleep(self.delay)
            return list(self.items)
        finally:
            self.gauge.exit()


class GaugedDistiller:
    """Fake distiller proving the write/distill stage stays serial."""

    def __init__(self, gauge: Gauge):
        self.gauge = gauge

    async def distill(self, raw: RawItem) -> DistilledItem:
        self.gauge.enter(raw.url)
        try:
            await asyncio.sleep(0.005)
            return DistilledItem(
                title_zh=f"中文:{raw.title}",
                summary_zh="摘要",
                key_points_zh=[],
                tags_zh=[],
            )
        finally:
            self.gauge.exit()


async def _mk_sources(n: int) -> list:
    return [
        await store.create_source(
            name=f"src-{i}", kind="rss", url=f"https://example{i}.com/feed"
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_fetches_overlap_but_writes_stay_serial(monkeypatch):
    await init_db()
    sources = await _mk_sources(3)

    fetch_gauge = Gauge()
    distill_gauge = Gauge()
    fetchers = {
        s.id: GaugedFetcher(fetch_gauge, [_raw(f"https://items/{s.id}", s.id)])
        for s in sources
    }
    monkeypatch.setattr(registry, "get_fetcher", lambda src: fetchers[src.id])
    monkeypatch.setattr(sync_mod, "_get_distiller", lambda: GaugedDistiller(distill_gauge))

    ids = [s.id for s in sources]
    job_id = await store.create_job(None, sources_total=len(ids))
    result = await orchestrator._run_pipeline_for_sources(ids, job_id)

    assert result.status == SyncJobStatus.done
    assert result.items_new == 3
    assert result.items_distilled == 3
    assert result.sources_done == 3

    # The point of the two-stage split: fetches overlapped …
    assert fetch_gauge.high_water >= 2, (
        f"expected concurrent fetches, high_water={fetch_gauge.high_water}"
    )
    # … while the DB-write/distill stage never ran two sources at once …
    assert distill_gauge.high_water == 1
    # … and consumed results in the ORIGINAL source order (one item per
    # source; the url tail is the source id).
    assert [u.rsplit("/", 1)[1] for u in distill_gauge.order] == ids


@pytest.mark.asyncio
async def test_slow_source_does_not_block_other_fetches(monkeypatch):
    """The slow source's fetch and the fast sources' fetches overlap —
    the fast ones finish while the slow head is still in flight."""
    await init_db()
    sources = await _mk_sources(3)
    slow_id = sources[0].id

    finished: list[str] = []

    class RecordingFetcher:
        def __init__(self, sid: str, delay: float):
            self.sid = sid
            self.delay = delay

        async def fetch(self, source, *, lookback_days: int | None = None):
            await asyncio.sleep(self.delay)
            finished.append(self.sid)
            return []

    fetchers = {
        s.id: RecordingFetcher(s.id, 0.08 if s.id == slow_id else 0.01)
        for s in sources
    }
    monkeypatch.setattr(registry, "get_fetcher", lambda src: fetchers[src.id])

    ids = [s.id for s in sources]
    job_id = await store.create_job(None, sources_total=len(ids))
    result = await orchestrator._run_pipeline_for_sources(ids, job_id)

    assert result.status == SyncJobStatus.done
    # The fast fetches completed BEFORE the slow head — impossible under
    # the old fully-serial pipeline, where the slow source ran first and
    # alone.
    assert finished[-1] == slow_id
    assert set(finished[:2]) == {sources[1].id, sources[2].id}


@pytest.mark.asyncio
async def test_concurrency_one_restores_serial_fetch(monkeypatch):
    await init_db()
    sources = await _mk_sources(3)

    fetch_gauge = Gauge()
    fetchers = {s.id: GaugedFetcher(fetch_gauge, []) for s in sources}
    monkeypatch.setattr(registry, "get_fetcher", lambda src: fetchers[src.id])
    monkeypatch.setattr(orchestrator.config, "SYNC_FETCH_CONCURRENCY", 1)

    ids = [s.id for s in sources]
    job_id = await store.create_job(None, sources_total=len(ids))
    result = await orchestrator._run_pipeline_for_sources(ids, job_id)

    assert result.status == SyncJobStatus.done
    assert result.sources_done == 3
    assert fetch_gauge.high_water == 1


@pytest.mark.asyncio
async def test_cancel_mid_run_lands_cancelled_on_source_boundary(monkeypatch):
    await init_db()
    sources = await _mk_sources(3)

    release = asyncio.Event()
    started = asyncio.Event()

    class BlockingFetcher:
        async def fetch(self, source, *, lookback_days: int | None = None):
            started.set()
            await release.wait()
            return []

    monkeypatch.setattr(registry, "get_fetcher", lambda src: BlockingFetcher())

    ids = [s.id for s in sources]
    job_id = await store.create_job(None, sources_total=len(ids))
    try:
        task = asyncio.create_task(
            orchestrator._run_pipeline_for_sources(ids, job_id)
        )
        # Wait until at least one fetch is genuinely in flight, then
        # cancel the job and unblock the fetchers.
        await asyncio.wait_for(started.wait(), timeout=2)
        orchestrator.cancelled_jobs.add(job_id)
        release.set()
        result = await asyncio.wait_for(task, timeout=5)
    finally:
        orchestrator.cancelled_jobs.discard(job_id)

    assert result.status == SyncJobStatus.cancelled
    # The head source was already mid-fetch when the cancel landed, so it
    # completes (per-source boundary); everything after it is skipped.
    assert result.sources_done == 1

    job = await store.get_job(job_id)
    assert job is not None
    assert job.status == SyncJobStatus.cancelled
