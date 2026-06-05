"""End-to-end API tests using FastAPI's TestClient (via asgi-lifespan)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from asgi_lifespan import LifespanManager

from prism_sidecar.app import app
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType, Source, SourceKind


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url, title=title, content=f"body {title}",
        published_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


class FakeFetcher:
    def __init__(self, items: list[RawItem]):
        self._items = items
        self.kind = SourceKind.rss

    async def fetch(self, source: Source) -> list[RawItem]:
        return list(self._items)


@pytest.fixture
async def client(monkeypatch):
    # Default: no distiller.
    from prism_sidecar import config
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", None)
    monkeypatch.setattr(config, "is_distiller_configured", lambda: False)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_health_endpoint(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "version" in body
    assert body["distillerConfigured"] is False
    assert "dbPath" in body


@pytest.mark.asyncio
async def test_sources_seeded_on_first_run(client):
    r = await client.get("/api/sources")
    assert r.status_code == 200
    sources = r.json()
    assert len(sources) == 5
    ids = {s["id"] for s in sources}
    assert {"src_hn", "src_simon", "src_openai", "src_anthropic", "src_huggingface"} <= ids


@pytest.mark.asyncio
async def test_create_and_delete_source(client):
    r = await client.post(
        "/api/sources",
        json={"name": "Custom", "kind": "rss", "url": "https://x", "enabled": True},
    )
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert r.json()["name"] == "Custom"

    r = await client.delete(f"/api/sources/{new_id}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = await client.get(f"/api/sources/{new_id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_source(client):
    r = await client.patch(
        "/api/sources/src_simon",
        json={"name": "Renamed", "enabled": False, "configJson": {"foo": 1}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["enabled"] is False
    assert body["configJson"] == {"foo": 1}


@pytest.mark.asyncio
async def test_items_have_bilingual_fields(client, monkeypatch):
    # Replace fetcher with deterministic data
    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([_raw("https://example.com/api-test", "API Test")]),
    )
    r = await client.post("/api/sync/src_simon")
    assert r.status_code == 200
    result = r.json()
    assert result["sourcesDone"] == 1
    assert result["itemsNew"] >= 1

    r = await client.get("/api/items?source_id=src_simon&limit=5")
    assert r.status_code == 200
    items = r.json()
    assert any(it["url"] == "https://example.com/api-test" for it in items)

    sample = next(it for it in items if it["url"] == "https://example.com/api-test")
    # Bilingual fields
    assert "titleEn" in sample
    assert sample["titleEn"] == "API Test"
    assert "titleZh" in sample  # may be None since no distiller
    # Compat fields
    assert sample["title"] == "API Test"  # falls back to en
    assert "summaryEn" in sample
    assert "summary" in sample


@pytest.mark.asyncio
async def test_items_limit_offset_and_search(client, monkeypatch):
    from prism_sidecar.fetchers import registry
    items_seed = [
        _raw(f"https://example.com/n{i}", f"Item {i} needle")
        for i in range(5)
    ] + [_raw("https://example.com/x", "Other stuff")]
    monkeypatch.setattr(registry, "get_fetcher", lambda src: FakeFetcher(items_seed))
    r = await client.post("/api/sync/src_simon")
    assert r.status_code == 200

    r = await client.get("/api/items?q=needle&limit=2&offset=0")
    body = r.json()
    assert len(body) == 2
    assert all("needle" in it["titleEn"] for it in body)


@pytest.mark.asyncio
async def test_sync_returns_409_on_concurrent(client, monkeypatch):
    from prism_sidecar.fetchers import registry

    # Use a slow fetcher so the first sync is still running when the
    # second hits.
    class SlowFetcher:
        kind = SourceKind.rss

        async def fetch(self, source):
            import asyncio
            await asyncio.sleep(0.5)
            return [_raw("https://example.com/slow", "Slow")]

    monkeypatch.setattr(registry, "get_fetcher", lambda src: SlowFetcher())

    import asyncio
    t1, t2 = await asyncio.gather(
        client.post("/api/sync/src_simon"),
        client.post("/api/sync/src_openai"),
    )
    # Exactly one should be 200 and the other 409
    codes = sorted([t1.status_code, t2.status_code])
    assert codes == [200, 409], f"unexpected codes: {codes}"
    rejected = t1 if t1.status_code == 409 else t2
    assert "already running" in rejected.json()["detail"]


@pytest.mark.asyncio
async def test_sync_history_endpoint(client, monkeypatch):
    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([_raw("https://example.com/hist", "Hist")]),
    )
    await client.post("/api/sync/src_simon")
    r = await client.get("/api/sync/history?limit=10")
    assert r.status_code == 200
    history = r.json()
    assert len(history) >= 1
    assert any(e["sourceId"] == "src_simon" for e in history)


@pytest.mark.asyncio
async def test_sync_status_endpoint(client, monkeypatch):
    from prism_sidecar.fetchers import registry
    monkeypatch.setattr(
        registry, "get_fetcher",
        lambda src: FakeFetcher([_raw("https://example.com/status", "Status")]),
    )
    r = await client.post("/api/sync/src_simon")
    job_id = r.json()["jobId"]
    r2 = await client.get(f"/api/sync/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["jobId"] == job_id
    assert body["status"] in {"done", "error"}
    assert body["finishedAt"] is not None
