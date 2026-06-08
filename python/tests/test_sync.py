"""Test the sync pipeline with mocked fetcher + distiller."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar import store
from prism_sidecar.db import init_db
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType, Source, SourceKind
from prism_sidecar.pipeline.sync import run_source_sync


class FakeFetcher:
    def __init__(self, items: list[RawItem]):
        self._items = items
        self.kind = SourceKind.rss
        # Record the lookback window the pipeline asked for, so tests can
        # assert first-sync vs subsequent-sync behaviour.
        self.last_lookback_days: int | None = None

    async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
        self.last_lookback_days = lookback_days
        return list(self._items)


class FakeDistiller:
    """Records every raw it sees; returns canned DistilledItem."""

    def __init__(self, fail_for: set[str] | None = None, key_invalid: bool = False):
        self.calls: list[RawItem] = []
        self.fail_for = fail_for or set()
        self.key_invalid = key_invalid

    async def distill(self, raw: RawItem) -> DistilledItem:
        if self.key_invalid:
            from prism_sidecar.distillers.base import DistillerKeyInvalid
            raise DistillerKeyInvalid("simulated: API key rejected")
        if raw.url in self.fail_for:
            raise RuntimeError("simulated distill failure")
        self.calls.append(raw)
        return DistilledItem(
            title_zh=f"中文:{raw.title}",
            summary_zh=f"中文摘要:{raw.title}",
            key_points_zh=[f"点{raw.title}"],
            tags_zh=["测试"],
        )


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url, title=title, content=f"body {title}",
        published_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


@pytest.mark.asyncio
async def test_run_source_sync_inserts_and_distills(monkeypatch):
    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    # Patch registry to return our fake fetcher
    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([
            _raw("https://example.com/a", "Alpha"),
            _raw("https://example.com/b", "Beta"),
        ]),
    )

    distiller = FakeDistiller()
    stats = await run_source_sync(source, distiller=distiller)

    assert stats.fetched == 2
    assert stats.new_items == 2
    assert stats.distilled == 2
    assert stats.failed_distill == 0

    items = await store.list_items(source_id=source.id)
    assert len(items) == 2
    by_url = {it.url: it for it in items}
    assert by_url["https://example.com/a"].title_zh == "中文:Alpha"
    assert by_url["https://example.com/b"].title_zh == "中文:Beta"


@pytest.mark.asyncio
async def test_run_source_sync_dedupes_by_url(monkeypatch):
    await init_db()
    source = await store.create_source("S", "rss", "https://x")
    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([_raw("https://example.com/dup", "First")]),
    )
    # First run: 1 new
    s1 = await run_source_sync(source, distiller=FakeDistiller())
    assert s1.new_items == 1
    # Second run with same URL: 0 new
    s2 = await run_source_sync(source, distiller=FakeDistiller())
    assert s2.new_items == 0
    # Still only 1 item in DB
    items = await store.list_items(source_id=source.id)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_run_source_sync_continues_on_distill_failure(monkeypatch):
    await init_db()
    source = await store.create_source("S", "rss", "https://x")
    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([
            _raw("https://example.com/ok", "OK"),
            _raw("https://example.com/bad", "BAD"),
        ]),
    )
    distiller = FakeDistiller(fail_for={"https://example.com/bad"})
    stats = await run_source_sync(source, distiller=distiller)
    assert stats.new_items == 2
    assert stats.distilled == 1
    assert stats.failed_distill == 1

    items = await store.list_items(source_id=source.id)
    by_url = {it.url: it for it in items}
    assert by_url["https://example.com/ok"].distilled_at is not None
    assert by_url["https://example.com/bad"].distilled_at is None
    # Raw item preserved
    assert by_url["https://example.com/bad"].title_en == "BAD"


@pytest.mark.asyncio
async def test_run_source_sync_handles_fetcher_error(monkeypatch):
    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    class BoomFetcher:
        kind = SourceKind.rss

        async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
            raise RuntimeError("network down")

    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(registry, "get_fetcher", lambda src: BoomFetcher())

    stats = await run_source_sync(source, distiller=FakeDistiller())
    assert stats.error is not None
    assert "network down" in stats.error
    # No items inserted
    items = await store.list_items(source_id=source.id)
    assert items == []


@pytest.mark.asyncio
async def test_run_source_sync_skips_disabled_source():
    await init_db()
    source = await store.create_source("S", "rss", "https://x", enabled=False)
    stats = await run_source_sync(source, distiller=FakeDistiller())
    assert stats.error == "source disabled"


@pytest.mark.asyncio
async def test_run_source_sync_uses_wide_lookback_on_first_run(monkeypatch):
    """First sync of a source should use INITIAL_FETCH_LOOKBACK_DAYS
    (default 30) so a fresh install gets real history. Subsequent syncs
    fall back to FETCH_LOOKBACK_DAYS (default 7)."""
    from prism_sidecar.config import (
        FETCH_LOOKBACK_DAYS,
        INITIAL_FETCH_LOOKBACK_DAYS,
    )
    from prism_sidecar.fetchers import registry

    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    fetcher = FakeFetcher([_raw("https://example.com/a", "A")])
    monkeypatch.setattr(registry, "get_fetcher", lambda src: fetcher)

    # First sync → wide lookback.
    await run_source_sync(source, distiller=FakeDistiller())
    assert fetcher.last_lookback_days == INITIAL_FETCH_LOOKBACK_DAYS

    # Second sync → narrow lookback.
    await run_source_sync(source, distiller=FakeDistiller())
    assert fetcher.last_lookback_days == FETCH_LOOKBACK_DAYS


@pytest.mark.asyncio
async def test_run_source_sync_aborts_on_key_invalid(monkeypatch):
    """When the distiller raises DistillerKeyInvalid, the source-level
    sync should mark stats.key_invalid=True and stop processing more
    raw items for that source (don't burn the dead key)."""
    from prism_sidecar.fetchers import registry

    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([
            _raw("https://example.com/a", "A"),
            _raw("https://example.com/b", "B"),
            _raw("https://example.com/c", "C"),
        ]),
    )

    distiller = FakeDistiller(key_invalid=True)
    stats = await run_source_sync(source, distiller=distiller)

    assert stats.key_invalid is True
    assert "key_invalid" in (stats.error or "")
    # Only the first raw is consumed before the key-invalid abort.
    assert stats.distilled == 0
    # Items are still inserted even though they didn't get distilled —
    # they stay pending for a later re-distill pass.
    items = await store.list_items(source_id=source.id)
    assert len(items) >= 1
