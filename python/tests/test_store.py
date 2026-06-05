"""Test the SQLite-backed store."""

from __future__ import annotations

import pytest

from prism_sidecar.db import init_db
from prism_sidecar import store
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType, SourceKind


@pytest.fixture
async def initialized():
    await init_db()
    yield


@pytest.mark.asyncio
async def test_create_and_list_sources(initialized):
    s = await store.create_source("My Blog", "rss", "https://example.com/feed")
    assert s.id.startswith("src_")
    assert s.name == "My Blog"
    assert s.config_json == {}
    sources = await store.list_sources()
    assert len(sources) == 1
    assert sources[0].id == s.id


@pytest.mark.asyncio
async def test_patch_source_updates_fields(initialized):
    s = await store.create_source("Old", "rss", "https://x")
    updated = await store.patch_source(
        s.id, name="New", enabled=False, config_json={"foo": "bar"},
    )
    assert updated is not None
    assert updated.name == "New"
    assert updated.enabled is False
    assert updated.config_json == {"foo": "bar"}


@pytest.mark.asyncio
async def test_delete_source(initialized):
    s = await store.create_source("Doomed", "rss", "https://x")
    assert await store.delete_source(s.id) is True
    assert await store.delete_source(s.id) is False
    assert await store.get_source(s.id) is None


@pytest.mark.asyncio
async def test_insert_item_dedupes_by_url(initialized):
    source = await store.create_source("S", "rss", "https://x")
    raw = RawItem(
        url="https://example.com/post-1",
        title="Hello",
        content="Body",
        published_at=__import__("datetime").datetime(2026, 6, 5, tzinfo=__import__("datetime").timezone.utc),
        content_type=ContentType.article,
    )
    assert await store.item_exists_by_url(raw.url) is False
    item_id = await store.insert_item_from_raw(source, raw)
    assert await store.item_exists_by_url(raw.url) is True

    # Update with distilled content
    await store.update_item_distilled(
        item_id,
        DistilledItem(
            title_zh="你好",
            summary_zh="摘要",
            key_points_zh=["一", "二"],
            tags_zh=["标签1"],
        ),
    )
    item = await store.get_item(item_id)
    assert item is not None
    assert item.title_zh == "你好"
    assert item.summary_zh == "摘要"
    assert item.title == "你好"  # compat shim
    assert item.summary == "摘要"  # compat shim
    assert item.key_points == ["一", "二"]
    assert item.tags == ["标签1"]
    assert item.distilled_at is not None


@pytest.mark.asyncio
async def test_compat_shims_fall_back_to_en(initialized):
    source = await store.create_source("S", "rss", "https://x")
    raw = RawItem(
        url="https://example.com/post-2",
        title="Original English Title",
        content="body",
        published_at=__import__("datetime").datetime(2026, 6, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    item_id = await store.insert_item_from_raw(source, raw)
    item = await store.get_item(item_id)
    # No zh yet → compat shims fall back to en
    assert item.title == "Original English Title"
    assert item.key_points == []
    assert item.tags == []


@pytest.mark.asyncio
async def test_list_items_filter_and_paginate(initialized):
    source = await store.create_source("S", "rss", "https://x")
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for i in range(7):
        raw = RawItem(
            url=f"https://example.com/post-{i}",
            title=f"Post {i} about AI",
            content="body",
            published_at=base + timedelta(hours=i),
        )
        await store.insert_item_from_raw(source, raw)

    # Filter by q
    items = await store.list_items(q="Post 3", limit=10, offset=0)
    assert len(items) == 1
    assert items[0].title_en == "Post 3 about AI"

    # Pagination
    page1 = await store.list_items(limit=3, offset=0)
    page2 = await store.list_items(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    assert page1[0].id != page2[0].id

    # Filter by source_id
    items = await store.list_items(source_id=source.id, limit=10)
    assert len(items) == 7


@pytest.mark.asyncio
async def test_sync_jobs_crud(initialized):
    job_id = await store.create_job(None)
    assert job_id.startswith("job_")
    job = await store.get_job(job_id)
    assert job is not None
    assert job.status.value == "running"
    assert job.source_id is None

    await store.update_job_progress(
        job_id, items_new=5, items_distilled=3, sources_done=1,
    )
    job = await store.get_job(job_id)
    assert job.items_new == 5
    assert job.items_distilled == 3
    assert job.sources_done == 1

    await store.finish_job(
        job_id,
        status=__import__("prism_sidecar.models", fromlist=["SyncJobStatus"]).SyncJobStatus.done,
        items_new=5, items_distilled=3, sources_total=1, sources_done=1,
    )
    job = await store.get_job(job_id)
    assert job.status.value == "done"
    assert job.finished_at is not None

    assert await store.is_any_job_running() is False


@pytest.mark.asyncio
async def test_sync_log_history(initialized):
    await store.write_sync_log(
        source_id="src_x", job_id="job_x",
        started_at="2026-06-05T10:00:00Z",
        finished_at="2026-06-05T10:00:10Z",
        items_new=2, items_distilled=1, error=None,
    )
    history = await store.list_sync_history(limit=10)
    assert len(history) == 1
    assert history[0].items_new == 2
    assert history[0].items_distilled == 1
