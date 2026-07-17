"""Test the sync pipeline with mocked fetcher + distiller."""

from __future__ import annotations

from datetime import datetime, timezone

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
    sync marks stats.key_invalid=True and stops DISTILLING (don't burn
    the dead key) — but keeps INSERTING the remaining raw items so
    they stay pending for a later redistill. The pre-fix `break`
    discarded every raw item after the failure point."""
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
    # Nothing was distilled…
    assert stats.distilled == 0
    # …but ALL fetched items were inserted (pending distillation).
    assert stats.new_items == 3
    items = await store.list_items(source_id=source.id)
    assert len(items) == 3
    assert all(it.distilled_at is None for it in items)
    # The distill-level error stays visible on the source row.
    refreshed = await store.get_source(source.id)
    assert refreshed is not None and "key_invalid" in (refreshed.last_error or "")


# ---- v0.2c: FetchError contract + failure cooldown -----------------------


@pytest.mark.asyncio
async def test_fetch_error_records_last_error_and_salvages_partials(monkeypatch):
    """整源 fetch 失败:last_error 落库、partial_items 不浪费、
    fail_streak 记账——这是"错误重新可见"设计的核心验收 case。"""
    from prism_sidecar.fetchers import registry
    from prism_sidecar.fetchers.base import FetchError
    from prism_sidecar.pipeline.sync import get_fail_streak, source_in_cooldown

    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    class PartialFailFetcher:
        kind = SourceKind.rss

        async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
            raise FetchError(
                "feed died mid-listing",
                partial_items=[_raw("https://example.com/salvaged", "Salvaged")],
            )

    monkeypatch.setattr(registry, "get_fetcher", lambda src: PartialFailFetcher())

    stats = await run_source_sync(source, distiller=FakeDistiller())

    # Error is visible in stats AND on the source row.
    assert stats.error is not None and "feed died" in stats.error
    refreshed = await store.get_source(source.id)
    assert refreshed is not None and "feed died" in (refreshed.last_error or "")

    # The partial item was still inserted + distilled.
    items = await store.list_items(source_id=source.id)
    assert len(items) == 1
    assert items[0].url == "https://example.com/salvaged"

    # Failure streak + cooldown recorded.
    assert await get_fail_streak(source.id) == 1
    assert await source_in_cooldown(source.id) is True


@pytest.mark.asyncio
async def test_fetch_error_does_not_consume_first_sync_window(monkeypatch):
    """fetch 失败的那次同步不该消耗 first-sync 宽窗口。"""
    from prism_sidecar.config import INITIAL_FETCH_LOOKBACK_DAYS
    from prism_sidecar.fetchers import registry
    from prism_sidecar.fetchers.base import FetchError

    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    class AlwaysFail:
        kind = SourceKind.rss
        last_lookback_days: int | None = None

        async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
            type(self).last_lookback_days = lookback_days
            raise FetchError("boom")

    monkeypatch.setattr(registry, "get_fetcher", lambda src: AlwaysFail())

    await run_source_sync(source, distiller=None)
    assert AlwaysFail.last_lookback_days == INITIAL_FETCH_LOOKBACK_DAYS
    # Second attempt STILL gets the wide window — it never succeeded.
    await run_source_sync(source, distiller=None)
    assert AlwaysFail.last_lookback_days == INITIAL_FETCH_LOOKBACK_DAYS


@pytest.mark.asyncio
async def test_success_clears_fail_streak_and_cooldown(monkeypatch):
    from prism_sidecar.fetchers import registry
    from prism_sidecar.fetchers.base import FetchError
    from prism_sidecar.pipeline.sync import (
        get_fail_streak,
        source_in_cooldown,
        source_retry_due,
    )

    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    class FailOnce:
        kind = SourceKind.rss
        calls = 0

        async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
            type(self).calls += 1
            if type(self).calls == 1:
                raise FetchError("first hit fails")
            return [_raw("https://example.com/ok", "OK")]

    monkeypatch.setattr(registry, "get_fetcher", lambda src: FailOnce())

    await run_source_sync(source, distiller=None)
    assert await get_fail_streak(source.id) == 1

    await run_source_sync(source, distiller=None)
    assert await get_fail_streak(source.id) == 0
    assert await source_in_cooldown(source.id) is False
    assert await source_retry_due(source.id) is False


@pytest.mark.asyncio
async def test_cooldown_escalates_with_streak(monkeypatch):
    """连续失败 → 冷却窗口指数增长,封顶 24h;non-retryable 直接 24h。"""
    from datetime import timedelta

    from prism_sidecar.pipeline.sync import (
        _retry_after_key,
        get_fail_streak,
        record_sync_failure,
    )
    from prism_sidecar.store import get_meta

    await init_db()
    source = await store.create_source("S", "rss", "https://x")

    async def _cooldown_hours() -> float:
        raw = await get_meta(_retry_after_key(source.id))
        until = datetime.fromisoformat(raw)
        return (until - datetime.now(timezone.utc)) / timedelta(hours=1)

    await record_sync_failure(source.id)          # streak 1 → 2h
    assert 1.9 < await _cooldown_hours() <= 2.0
    await record_sync_failure(source.id)          # streak 2 → 4h
    assert 3.9 < await _cooldown_hours() <= 4.0
    for _ in range(4):                            # streak 6 → capped 24h
        await record_sync_failure(source.id)
    assert 23.9 < await _cooldown_hours() <= 24.0
    assert await get_fail_streak(source.id) == 6

    # Non-retryable failure jumps straight to the cap.
    await record_sync_failure("other_src", retryable=False)
    raw = await get_meta(_retry_after_key("other_src"))
    until = datetime.fromisoformat(raw)
    hours = (until - datetime.now(timezone.utc)) / timedelta(hours=1)
    assert 23.9 < hours <= 24.0


@pytest.mark.asyncio
async def test_real_rss_fetcher_through_pipeline(monkeypatch):
    """真实 RSSFetcher 走通 run_source_sync 记账路径——不是 Fake。
    (test_sync 全用 Fake fetcher 曾漏掉 lookback_days 签名 bug。)"""
    import respx
    from httpx import Response

    from prism_sidecar.fetchers import registry
    from prism_sidecar.fetchers.rss import RSSFetcher

    await init_db()
    source = await store.create_source("S", "rss", "https://feed.example.com/rss.xml")

    monkeypatch.setattr(registry, "get_fetcher", lambda src: RSSFetcher(max_retries=0))

    with respx.mock() as mock:
        mock.get("https://feed.example.com/rss.xml").mock(
            return_value=Response(500, text="server error")
        )
        stats = await run_source_sync(source, distiller=None)

    # The real fetcher raised FetchError; the pipeline recorded it.
    assert stats.error is not None and "fetch" in stats.error
    refreshed = await store.get_source(source.id)
    assert refreshed is not None and refreshed.last_error
