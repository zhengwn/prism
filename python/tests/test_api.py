"""End-to-end API tests using FastAPI's TestClient (via asgi-lifespan)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from prism_sidecar.app import app
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType, Source, SourceKind


def _raw(url: str, title: str) -> RawItem:
    return RawItem(
        url=url, title=title, content=f"body {title}",
        published_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        content_type=ContentType.article,
    )


async def _wait_for_job(client, job_id: str, *, timeout: float = 5.0) -> dict:
    """Poll /api/sync/{job_id} until the job finishes.

    v0.2b made /api/sync return immediately with status=running,
    so any test that previously did `r = await client.post(...)`
    and then read `r.json()` to get the *final* result now needs
    to wait for the background pipeline to finish. This helper
    bounds the wait so a deadlock fails fast instead of hanging
    the whole test run.
    """
    import asyncio
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        r = await client.get(f"/api/sync/{job_id}")
        body = r.json()
        if body["status"] in {"done", "error", "cancelled"}:
            return body
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(f"job {job_id} did not finish within {timeout}s: {body}")
        await asyncio.sleep(0.05)


async def _post_sync_and_wait(client, path: str = "/api/sync") -> dict:
    """POST /api/sync (or /api/sync/{id}) and wait for the
    background pipeline to finish. Returns the final SyncResult
    body, equivalent to what the v0.2a synchronous route returned.
    """
    r = await client.post(path)
    assert r.status_code == 200, r.text
    initial = r.json()
    return await _wait_for_job(client, initial["jobId"])


class FakeFetcher:
    def __init__(self, items: list[RawItem]):
        self._items = items
        self.kind = SourceKind.rss

    async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
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
    # v0.2b PoC: 5 RSS + 3 B 站 UP 主 = 8. The bilibili seeds must
    # come back with kind=bilibili and configJson.mid populated.
    assert len(sources) == 8
    ids = {s["id"] for s in sources}
    assert {
        "src_hn", "src_simon", "src_openai", "src_anthropic", "src_huggingface",
        "src_bili_zhidongxi", "src_bili_jiqizhixin", "src_bili_paperweekly",
    } <= ids
    # The 3 B 站 seeds must surface as bilibili kind + correct mid in config_json.
    # Mid values are verified against B 站 search API on 2026-06-16
    # (see fixtures.py for the verification command).
    by_id = {s["id"]: s for s in sources}
    for bid, expected_mid in [
        ("src_bili_zhidongxi", "31703119"),
        ("src_bili_jiqizhixin", "73414544"),
        ("src_bili_paperweekly", "368145665"),
    ]:
        src = by_id[bid]
        assert src["kind"] == "bilibili", f"{bid} kind={src['kind']!r}"
        assert src["configJson"]["mid"] == expected_mid, (
            f"{bid} mid={src['configJson'].get('mid')!r}"
        )


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
    initial = r.json()
    result = await _wait_for_job(client, initial["jobId"])
    assert result["status"] == "done", result
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
    result = await _post_sync_and_wait(client, "/api/sync/src_simon")
    assert result["status"] == "done"

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

        async def fetch(self, source, lookback_days: int = 7):
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
    initial = await client.post("/api/sync/src_simon")
    job_id = initial.json()["jobId"]
    await _wait_for_job(client, job_id)
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
    # v0.2b: /api/sync returns immediately with status=running.
    # The status endpoint should reflect that, and the finishedAt
    # field should be None until the pipeline settles.
    r = await client.post("/api/sync/src_simon")
    assert r.status_code == 200
    initial = r.json()
    job_id = initial["jobId"]
    assert initial["status"] == "running"
    assert initial["finishedAt"] is None
    # Wait for completion, then re-check.
    final = await _wait_for_job(client, job_id)
    assert final["status"] == "done"
    assert final["finishedAt"] is not None


@pytest.mark.asyncio
async def test_cancel_unknown_job_returns_404(client):
    r = await client.post("/api/sync/job_does_not_exist/cancel")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_finished_job_returns_409(client):
    """Cancelling a job that already completed is a no-op the
    UI shouldn't see as success — 409 with a useful message so
    the button can show 'already done' instead of flashing green.
    """
    from prism_sidecar.fetchers import registry
    monkeypatch_fetcher = registry.get_fetcher
    registry.get_fetcher = lambda src: FakeFetcher([])  # type: ignore[assignment]
    try:
        initial = await client.post("/api/sync/src_simon")
        job_id = initial.json()["jobId"]
        # Wait for it to actually finish so cancel hits 409.
        await _wait_for_job(client, job_id)
        r2 = await client.post(f"/api/sync/{job_id}/cancel")
        assert r2.status_code == 409
        assert "already" in r2.json()["detail"].lower()
    finally:
        registry.get_fetcher = monkeypatch_fetcher  # type: ignore[assignment]


class _SlowCancelFetcher:
    """A fetcher that sleeps long enough for a cancel request to
    land mid-run. The pipeline polls the cancel flag BETWEEN
    sources, so we need at least two sources to actually exercise
    the check; this one just hangs long enough on the first
    source to give the test time to fire the cancel HTTP call."""

    def __init__(self) -> None:
        self.kind = SourceKind.rss
        self.first_call_done = False

    async def fetch(self, source: Source, lookback_days: int = 7) -> list[RawItem]:
        import asyncio as _asyncio
        if not self.first_call_done:
            self.first_call_done = True
            # Sleep long enough for the test to POST /cancel.
            # The pipeline will still finish this source (we
            # don't interrupt in-flight fetches) but should bail
            # before processing the next one.
            await _asyncio.sleep(0.3)
        return [_raw(f"https://example.com/slow-{source.id}", f"Slow {source.name}")]


@pytest.mark.asyncio
async def test_cancel_marks_job_as_cancelled(client, monkeypatch):
    """User cancels mid-run: the response should eventually be
    `status=cancelled` with partial progress preserved."""
    import asyncio as _asyncio

    from prism_sidecar import app as appmod
    from prism_sidecar.fetchers import registry

    # Ensure we have at least two enabled sources so the pipeline
    # has somewhere to bail (the cancel check is between sources).
    r = await client.get("/api/sources")
    sources = {s["id"]: s for s in r.json()}
    if "src_second" not in sources:
        await client.post("/api/sources", json={
            "id": "src_second", "name": "Second", "kind": "rss", "url": "https://x", "enabled": True,
        })
    if not sources.get("src_second", {}).get("enabled"):
        await client.patch("/api/sources/src_second", json={"enabled": True})

    slow = _SlowCancelFetcher()
    monkeypatch.setattr(registry, "get_fetcher", lambda src: slow)

    # v0.2b: /api/sync returns immediately with status=running
    # and the pipeline runs in the background. We use the
    # returned jobId to poll for the result, and POST cancel
    # against the same id once we've confirmed the pipeline is
    # actually working on it.
    r = await client.post("/api/sync")
    assert r.status_code == 200
    job_id = r.json()["jobId"]
    # Tiny delay so the background task is mid-source by the
    # time we cancel — otherwise the cancel flag arrives before
    # the first source's fetch starts and the test degenerates
    # to a normal done run.
    await _asyncio.sleep(0.05)
    assert job_id in appmod._inflight_jobs, "job should be inflight right after POST"

    # Cancel it.
    r = await client.post(f"/api/sync/{job_id}/cancel")
    assert r.status_code == 200
    assert r.json()["cancelled"] is True

    # Wait for the sync to settle.
    result = await _wait_for_job(client, job_id)
    assert result["status"] == "cancelled", result
    # Sources-done is between 0 and total — we never know exactly
    # which side of the cancel check the slow source landed on,
    # but the run must NOT have walked the full list.
    assert 0 < result["sourcesDone"] < result["sourcesTotal"]
    # The inflight set must be clean now.
    assert job_id not in appmod._inflight_jobs
