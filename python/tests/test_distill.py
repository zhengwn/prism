"""Test the redistill pipeline (Settings → '重蒸馏所有 pending' button)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar import store
from prism_sidecar.db import init_db
from prism_sidecar.distillers.base import (
    DistilledItem,
    DistillerKeyInvalid,
)
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType, Source, SourceKind
from prism_sidecar.pipeline.distill import (
    RedistillResult,
    list_pending_distill_ids,
    redistill_all_pending,
)


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url, title=title, content=f"body {title}",
        published_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


class FakeDistiller:
    def __init__(
        self,
        results: dict[str, DistilledItem] | None = None,
        fail_for: set[str] | None = None,
        key_invalid: bool = False,
    ) -> None:
        self.results = results or {}
        self.fail_for = fail_for or set()
        self.key_invalid = key_invalid
        self.calls: list[str] = []

    async def distill(self, raw: RawItem) -> DistilledItem:
        self.calls.append(raw.url)
        if self.key_invalid:
            raise DistillerKeyInvalid("simulated: key rejected")
        if raw.url in self.fail_for:
            raise RuntimeError("simulated distill failure")
        if raw.url in self.results:
            return self.results[raw.url]
        # Sensible default
        return DistilledItem(
            title_zh=f"中文:{raw.title}",
            summary_zh=f"中文摘要:{raw.title}",
            key_points_zh=[f"点{raw.title}"],
            tags_zh=["测试"],
        )


async def _seed_pending_items(n: int) -> str:
    """Insert `n` items with `distilled_at=NULL` and return the source id."""
    await init_db()
    source = await store.create_source("S", "rss", "https://x")
    for i in range(n):
        # Insert raw items directly via the store helper. We bypass the
        # fetcher/distiller (which would mark them distilled) to set up
        # the "pending" state.
        raw = _raw(f"https://example.com/{i}", f"Post{i}")
        await store.insert_item_from_raw(source, raw)
    return source.id


@pytest.mark.asyncio
async def test_list_pending_distill_ids_returns_only_pending():
    src = await _seed_pending_items(3)
    pending = await list_pending_distill_ids()
    assert len(pending) == 3
    items = await store.list_items(source_id=src)
    assert all(item.distilled_at is None for item in items)


@pytest.mark.asyncio
async def test_redistill_all_pending_distills_them_all():
    await _seed_pending_items(3)
    distiller = FakeDistiller()
    result = await redistill_all_pending(distiller=distiller)

    assert isinstance(result, RedistillResult)
    assert result.started_pending == 3
    assert result.distilled == 3
    assert result.failed == 0
    assert result.key_invalid is False
    assert result.error is None
    # Verify the DB rows were actually updated.
    pending = await list_pending_distill_ids()
    assert pending == []


@pytest.mark.asyncio
async def test_redistill_all_pending_records_partial_failures():
    await _seed_pending_items(3)
    distiller = FakeDistiller(fail_for={"https://example.com/1"})
    result = await redistill_all_pending(distiller=distiller)

    assert result.started_pending == 3
    assert result.distilled == 2
    assert result.failed == 1
    assert any("example.com/1" in f for f in result.sample_failures)
    # 1 item should still be pending.
    pending = await list_pending_distill_ids()
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_redistill_all_pending_stops_on_key_invalid():
    await _seed_pending_items(5)
    distiller = FakeDistiller(key_invalid=True)
    result = await redistill_all_pending(distiller=distiller)

    assert result.key_invalid is True
    assert "key_invalid" in (result.error or "")
    # Should stop at the first item — none distilled, all still pending.
    assert result.distilled == 0
    pending = await list_pending_distill_ids()
    assert len(pending) == 5


@pytest.mark.asyncio
async def test_redistill_all_pending_respects_batch_limit():
    await _seed_pending_items(5)
    distiller = FakeDistiller()
    result = await redistill_all_pending(distiller=distiller, batch_limit=2)

    # Only the first 2 should have been touched.
    assert result.started_pending == 2
    assert result.distilled == 2
    # Remaining 3 still pending — caller can invoke again.
    pending = await list_pending_distill_ids()
    assert len(pending) == 3
