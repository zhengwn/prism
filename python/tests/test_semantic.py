"""Semantic search (v0.5): reindex + KNN over sqlite-vec, fake embedder.

Uses a deterministic marker-based fake embedder so KNN ordering is
predictable, against the REAL sqlite-vec vec0 table (loaded by init_db).
No network / MiniMax key involved.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from prism_sidecar import embeddings as emb
from prism_sidecar import search, store
from prism_sidecar.app import app
from prism_sidecar.db import init_db, vec_available
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType

MARKERS = ["alpha", "beta", "gamma"]


def _marker_vec(text: str) -> list[float]:
    """One-hot-ish vector keyed on which marker word the text contains, so
    a query for 'alpha …' is nearest to the alpha item."""
    v = [0.0] * emb.EMBED_DIM
    for i, m in enumerate(MARKERS):
        if m in text:
            v[i] = 1.0
            return v
    v[emb.EMBED_DIM - 1] = 1.0
    return v


async def _fake_embed(texts, *, kind):  # noqa: ANN001 - test double
    return [_marker_vec(t) for t in texts]


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url, title=title, content=f"body {title}",
        published_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


@pytest.fixture
async def indexed(monkeypatch):
    """A source with three distilled items (alpha/beta/gamma), a working
    fake embedder, and embeddings marked available."""
    monkeypatch.setattr(emb, "embed_texts", _fake_embed)
    monkeypatch.setattr(emb, "embeddings_available", lambda: True)

    await init_db()
    src = await store.create_source("S", "rss", "https://x")
    ids = {}
    for m in MARKERS:
        iid = await store.insert_item_from_raw(src, _raw(f"https://ex/{m}", f"{m} title"))
        await store.update_item_distilled(
            iid, DistilledItem(title_zh=f"{m} 标题", summary_zh=f"{m} 摘要")
        )
        ids[m] = iid
    return src, ids


# ----- reindex + KNN -------------------------------------------------------

@pytest.mark.asyncio
async def test_vec_table_available(indexed):
    assert vec_available() is True


@pytest.mark.asyncio
async def test_reindex_embeds_all_distilled(indexed):
    res = await search.reindex_missing()
    assert res["available"] is True
    assert res["indexed"] == 3
    assert res["remaining"] == 0
    assert await store.count_vectors() == 3
    # Idempotent — nothing left to do on a second pass.
    again = await search.reindex_missing()
    assert again["indexed"] == 0


@pytest.mark.asyncio
async def test_semantic_search_ranks_by_marker(indexed):
    _, ids = indexed
    await search.reindex_missing()
    hits = await search.semantic_search("alpha please", limit=3)
    assert hits, "expected at least one hit"
    assert hits[0].id == ids["alpha"]  # nearest neighbour is the alpha item


@pytest.mark.asyncio
async def test_semantic_search_source_filter(indexed):
    src, ids = indexed
    await search.reindex_missing()
    # A second source with its own alpha item.
    src2 = await store.create_source("S2", "rss", "https://y")
    other = await store.insert_item_from_raw(src2, _raw("https://y/a", "alpha two"))
    await store.update_item_distilled(other, DistilledItem(title_zh="alpha 二", summary_zh="alpha"))
    await search.embed_item(other)

    hits = await search.semantic_search("alpha", limit=10, source_id=src2.id)
    assert [h.id for h in hits] == [other]


@pytest.mark.asyncio
async def test_embed_item_keeps_index_fresh(indexed):
    src, _ = indexed
    assert await store.count_vectors() == 0  # nothing indexed yet
    new_id = await store.insert_item_from_raw(src, _raw("https://ex/d", "delta title"))
    await store.update_item_distilled(new_id, DistilledItem(title_zh="delta", summary_zh="d"))
    ok = await search.embed_item(new_id)
    assert ok is True
    assert await store.count_vectors() == 1


# ----- unavailable path ----------------------------------------------------

@pytest.mark.asyncio
async def test_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(emb, "embeddings_available", lambda: False)
    await init_db()
    assert search.available() is False
    assert await search.semantic_search("anything") == []
    res = await search.reindex_missing()
    assert res["available"] is False
    assert "reason" in res


# ----- API -----------------------------------------------------------------

@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(emb, "embed_texts", _fake_embed)
    monkeypatch.setattr(emb, "embeddings_available", lambda: True)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _seed(client) -> dict:
    r = await client.post("/api/sources", json={"name": "S", "kind": "rss", "url": "https://x"})
    src_id = r.json()["id"]
    await init_db()
    ids = {}
    for m in MARKERS:
        iid = await store.insert_item_from_raw(
            await store.get_source(src_id), _raw(f"https://ex/{m}", f"{m} title")
        )
        await store.update_item_distilled(iid, DistilledItem(title_zh=f"{m} 标题", summary_zh=m))
        ids[m] = iid
    return ids


@pytest.mark.asyncio
async def test_api_status_reindex_semantic(client):
    ids = await _seed(client)

    r = await client.get("/api/search/status")
    st = r.json()
    assert st["available"] is True and st["pending"] == 3 and st["indexed"] == 0

    r = await client.post("/api/search/reindex")
    assert r.json()["indexed"] == 3

    r = await client.get("/api/search/status")
    assert r.json()["indexed"] == 3 and r.json()["pending"] == 0

    r = await client.get("/api/search/semantic", params={"q": "gamma", "limit": 3})
    assert r.status_code == 200
    assert r.json()[0]["id"] == ids["gamma"]


@pytest.mark.asyncio
async def test_api_semantic_empty_when_unavailable(client, monkeypatch):
    await _seed(client)
    monkeypatch.setattr(emb, "embeddings_available", lambda: False)
    r = await client.get("/api/search/semantic", params={"q": "alpha"})
    assert r.status_code == 200
    assert r.json() == []
