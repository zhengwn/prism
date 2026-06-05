"""Test the RSS fetcher with mocked httpx responses."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import respx
from httpx import Response

from prism_sidecar.config import FETCH_TIMEOUT_SEC
from prism_sidecar.fetchers.rss import RSSFetcher, _strip_html
from prism_sidecar.models import Source, SourceKind


SAMPLE_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Feed</title>
  <link href="https://example.com/"/>
  <updated>2026-06-05T08:00:00Z</updated>
  <entry>
    <title>First post about LLMs</title>
    <link href="https://example.com/post-1"/>
    <id>urn:uuid:1</id>
    <updated>2026-06-05T08:00:00Z</updated>
    <author><name>Alice</name></author>
    <summary type="html">&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;!&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Second post about agents</title>
    <link href="https://example.com/post-2"/>
    <id>urn:uuid:2</id>
    <updated>2026-06-04T08:00:00Z</updated>
    <summary>An &amp; an &amp;amp; entity test</summary>
  </entry>
</feed>
"""


SAMPLE_OLD = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Old Feed</title>
  <updated>2020-01-01T00:00:00Z</updated>
  <entry>
    <title>Ancient post</title>
    <link href="https://example.com/old"/>
    <updated>2020-01-01T00:00:00Z</updated>
  </entry>
</feed>
"""


def _make_source(url: str = "https://example.com/feed.xml") -> Source:
    return Source(
        id="src_test",
        name="Test",
        kind=SourceKind.rss,
        url=url,
        enabled=True,
    )


def test_strip_html_basic():
    assert _strip_html("<p>Hello <b>world</b>!</p>") == "Hello world !"
    assert _strip_html("Plain text") == "Plain text"
    assert _strip_html("") == ""
    assert _strip_html("<br/>") == ""
    assert _strip_html("A &amp; B") == "A & B"
    assert _strip_html("A &amp;amp; B") == "A &amp; B"


@pytest.mark.asyncio
async def test_rss_fetcher_parses_atom():
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC, lookback_days=7)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/feed.xml").mock(
            return_value=Response(200, content=SAMPLE_ATOM)
        )
        source = _make_source()
        items = await fetcher.fetch(source)

    assert len(items) == 2
    first = items[0]
    assert first.url == "https://example.com/post-1"
    assert first.title == "First post about LLMs"
    assert "Hello world" in first.content
    assert first.author == "Alice"
    assert first.published_at.tzinfo is not None


@pytest.mark.asyncio
async def test_rss_fetcher_drops_old_entries():
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC, lookback_days=7)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/feed.xml").mock(
            return_value=Response(200, content=SAMPLE_OLD)
        )
        items = await fetcher.fetch(_make_source())
    assert items == []


@pytest.mark.asyncio
async def test_rss_fetcher_retries_on_5xx():
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC, max_retries=2, retry_backoff=0.01)
    with respx.mock() as mock:
        route = mock.get("https://example.com/feed.xml").mock(
            side_effect=[
                Response(503, text="service unavailable"),
                Response(200, content=SAMPLE_ATOM),
            ]
        )
        items = await fetcher.fetch(_make_source())
    assert len(items) == 2
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_rss_fetcher_returns_empty_on_total_failure():
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC, max_retries=1, retry_backoff=0.01)
    with respx.mock() as mock:
        mock.get("https://example.com/feed.xml").mock(
            return_value=Response(404, text="not found")
        )
        items = await fetcher.fetch(_make_source())
    assert items == []
