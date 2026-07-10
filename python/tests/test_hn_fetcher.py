"""Test the Hacker News (Algolia) fetcher."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from prism_sidecar.fetchers.hackernews import HackerNewsFetcher, is_hn_source
from prism_sidecar.models import Source, SourceKind


SAMPLE_HITS = {
    "hits": [
        {
            "objectID": "1",
            "title": "Show HN: My new AI agent framework",
            "url": "https://example.com/agent",
            "created_at": "2026-06-04T10:00:00Z",
            "author": "alice",
            "points": 123,
        },
        {
            "objectID": "2",
            "title": "GPT-5 release notes",
            "url": "https://openai.com/gpt-5",
            "created_at": "2026-06-03T08:00:00Z",
            "author": "bob",
            "points": 999,
        },
        {
            # Story with no external url — should be skipped (HN discussion post)
            "objectID": "3",
            "title": "Ask HN: Best LLM eval framework?",
            "created_at": "2026-06-02T08:00:00Z",
            "author": "carol",
        },
    ]
}


def _make_source(query: str = "AI", keywords: list[str] | None = None) -> Source:
    cfg = {"is_hn_algolia": True}
    if keywords is not None:
        cfg["keywords"] = keywords
    else:
        cfg["keywords"] = [query]
    return Source(
        id="src_hn",
        name="Hacker News",
        kind=SourceKind.rss,
        url="https://hn.algolia.com/api/v1/search?tags=story&query=AI&hitsPerPage=20",
        config_json=cfg,
    )


def test_is_hn_source():
    s1 = _make_source()
    assert is_hn_source(s1) is True
    s2 = Source(
        id="x", name="x", kind=SourceKind.rss,
        url="https://hn.algolia.com/api/v1/search",
    )
    assert is_hn_source(s2) is True
    s3 = Source(
        id="y", name="y", kind=SourceKind.rss,
        url="https://simonwillison.net/atom/everything/",
    )
    assert is_hn_source(s3) is False


@pytest.mark.asyncio
async def test_hn_fetcher_returns_deduped_items():
    fetcher = HackerNewsFetcher(hits_per_page=20, max_retries=0)
    with respx.mock() as mock:
        # Single keyword query.
        mock.get("https://hn.algolia.com/api/v1/search").mock(
            return_value=Response(200, json=SAMPLE_HITS)
        )
        items = await fetcher.fetch(_make_source(query="AI"))

    # 2 of 3 hits have a URL — the 3rd (Ask HN with no url) is dropped.
    assert len(items) == 2
    urls = {it.url for it in items}
    assert "https://example.com/agent" in urls
    assert "https://openai.com/gpt-5" in urls

    # Sorted newest first
    assert items[0].published_at >= items[1].published_at
    assert items[0].metadata["hn_object_id"] == "1"


@pytest.mark.asyncio
async def test_hn_fetcher_dedupes_across_keywords():
    fetcher = HackerNewsFetcher(hits_per_page=20, max_retries=0)
    with respx.mock() as mock:
        mock.get("https://hn.algolia.com/api/v1/search").mock(
            return_value=Response(200, json=SAMPLE_HITS)
        )
        items = await fetcher.fetch(_make_source(keywords=["AI", "GPT", "agent"]))

    # Same hit list returned for every query, but deduped by objectID
    assert len(items) == 2


@pytest.mark.asyncio
async def test_hn_fetcher_accepts_lookback_days_kwarg():
    """Regression test — see the matching test in test_rss_fetcher.py.
    `pipeline/sync.py` always calls `fetcher.fetch(source, lookback_days=...)`;
    HackerNewsFetcher has no real lookback concept (it fetches all-time and
    dedupes by objectID) but MUST still accept the keyword or the pipeline
    call raises `TypeError` for every HN sync.
    """
    fetcher = HackerNewsFetcher(hits_per_page=20, max_retries=0)
    with respx.mock() as mock:
        mock.get("https://hn.algolia.com/api/v1/search").mock(
            return_value=Response(200, json=SAMPLE_HITS)
        )
        items = await fetcher.fetch(_make_source(query="AI"), lookback_days=7)
    assert len(items) == 2
