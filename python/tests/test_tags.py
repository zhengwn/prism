"""User-tag CRUD + filtering (v0.5)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from asgi_lifespan import LifespanManager

from prism_sidecar.app import app
from prism_sidecar.db import init_db
from prism_sidecar import store
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url,
        title=title,
        content=f"body {title}",
        published_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


@pytest.fixture
async def two_items():
    """A source with two items; yields (source, item_a_id, item_b_id)."""
    await init_db()
    source = await store.create_source("S", "rss", "https://x")
    a = await store.insert_item_from_raw(source, _raw("https://ex/a", "Alpha"))
    b = await store.insert_item_from_raw(source, _raw("https://ex/b", "Beta"))
    yield source, a, b


# ----- store layer --------------------------------------------------------

@pytest.mark.asyncio
async def test_add_list_remove_tag_roundtrip(two_items):
    _, a, _ = two_items
    item = await store.add_item_tag(a, "读过")
    assert item is not None
    assert item.user_tags == ["读过"]

    # get_item and list_items both carry user_tags.
    assert (await store.get_item(a)).user_tags == ["读过"]

    removed = await store.remove_item_tag(a, "读过")
    assert removed.user_tags == []


@pytest.mark.asyncio
async def test_add_tag_is_idempotent(two_items):
    _, a, _ = two_items
    await store.add_item_tag(a, "fav")
    await store.add_item_tag(a, "fav")
    item = await store.get_item(a)
    assert item.user_tags == ["fav"]


@pytest.mark.asyncio
async def test_remove_absent_tag_is_noop(two_items):
    _, a, _ = two_items
    item = await store.remove_item_tag(a, "never-added")
    assert item is not None
    assert item.user_tags == []


@pytest.mark.asyncio
async def test_tag_ops_on_missing_item_return_none(two_items):
    assert await store.add_item_tag("itm_missing", "x") is None
    assert await store.remove_item_tag("itm_missing", "x") is None


@pytest.mark.parametrize("bad", ["", "   ", "a\x1fb", "line\nbreak", "x" * 51])
@pytest.mark.asyncio
async def test_invalid_tags_rejected(two_items, bad):
    _, a, _ = two_items
    with pytest.raises(ValueError):
        await store.add_item_tag(a, bad)


@pytest.mark.asyncio
async def test_tag_is_trimmed(two_items):
    _, a, _ = two_items
    item = await store.add_item_tag(a, "  spaced  ")
    assert item.user_tags == ["spaced"]


@pytest.mark.asyncio
async def test_list_user_tags_counts_and_order(two_items):
    _, a, b = two_items
    await store.add_item_tag(a, "shared")
    await store.add_item_tag(b, "shared")
    await store.add_item_tag(a, "solo")

    tags = await store.list_user_tags()
    # Most-used first, then alphabetical.
    assert [(t.tag, t.count) for t in tags] == [("shared", 2), ("solo", 1)]


@pytest.mark.asyncio
async def test_list_items_tag_filter(two_items):
    _, a, b = two_items
    await store.add_item_tag(a, "keep")

    only_a = await store.list_items(tag="keep")
    assert [it.id for it in only_a] == [a]

    # Filtering by an unused tag yields nothing.
    assert await store.list_items(tag="nope") == []


@pytest.mark.asyncio
async def test_list_items_tag_filter_on_fts_path(two_items):
    _, a, b = two_items
    await store.add_item_tag(a, "keep")
    # q triggers the FTS path; the tag filter must still apply there.
    both_match_q = await store.list_items(q="body", tag="keep")
    assert [it.id for it in both_match_q] == [a]


@pytest.mark.asyncio
async def test_tags_cascade_when_source_deleted(two_items):
    source, a, _ = two_items
    await store.add_item_tag(a, "doomed")
    await store.delete_source(source.id)
    # item_tags rows should be gone (items → item_tags ON DELETE CASCADE).
    from prism_sidecar.db import get_db
    cur = await get_db().execute("SELECT COUNT(*) FROM item_tags")
    assert (await cur.fetchone())[0] == 0


# ----- API layer ----------------------------------------------------------

@pytest.fixture
async def client():
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _make_item(client) -> str:
    r = await client.post(
        "/api/sources",
        json={"name": "S", "kind": "rss", "url": "https://x"},
    )
    source_id = r.json()["id"]
    # Insert an item directly via the store (no fetcher needed).
    await init_db()
    return await store.insert_item_from_raw(
        await store.get_source(source_id), _raw("https://ex/a", "Alpha")
    )


@pytest.mark.asyncio
async def test_api_add_and_remove_tag(client):
    item_id = await _make_item(client)

    r = await client.post(f"/api/items/{item_id}/tags", json={"tag": "重要"})
    assert r.status_code == 200
    assert r.json()["userTags"] == ["重要"]

    r = await client.get("/api/tags")
    assert r.status_code == 200
    assert r.json() == [{"tag": "重要", "count": 1}]

    r = await client.delete(f"/api/items/{item_id}/tags/重要")
    assert r.status_code == 200
    assert r.json()["userTags"] == []


@pytest.mark.asyncio
async def test_api_add_tag_invalid_is_400(client):
    item_id = await _make_item(client)
    r = await client.post(f"/api/items/{item_id}/tags", json={"tag": "   "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_api_add_tag_missing_item_is_404(client):
    r = await client.post("/api/items/itm_missing/tags", json={"tag": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_items_tag_filter(client):
    item_id = await _make_item(client)
    await client.post(f"/api/items/{item_id}/tags", json={"tag": "keep"})

    r = await client.get("/api/items", params={"tag": "keep"})
    assert [it["id"] for it in r.json()] == [item_id]
    r = await client.get("/api/items", params={"tag": "absent"})
    assert r.json() == []
