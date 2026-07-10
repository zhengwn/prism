"""Test the RSS fetcher with mocked httpx responses."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import respx
from httpx import Response

from prism_sidecar.config import FETCH_TIMEOUT_SEC
from prism_sidecar.fetchers.base import FetchError
from prism_sidecar.fetchers.rss import RSSFetcher, _strip_html
from prism_sidecar.models import Source, SourceKind


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Entry timestamps are computed relative to "now" (not hardcoded absolute
# dates) so this test doesn't silently start failing once enough wall-clock
# time passes for the fixed dates to fall outside the fetcher's lookback
# window. (A previous version of this file hardcoded 2026-06-04/05 against
# a 7-day lookback — it broke the moment the test was run more than a week
# after that date.)
def _make_atom(recent_a: datetime, recent_b: datetime) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Feed</title>
  <link href="https://example.com/"/>
  <updated>{_iso(recent_a)}</updated>
  <entry>
    <title>First post about LLMs</title>
    <link href="https://example.com/post-1"/>
    <id>urn:uuid:1</id>
    <updated>{_iso(recent_a)}</updated>
    <author><name>Alice</name></author>
    <summary type="html">&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;!&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Second post about agents</title>
    <link href="https://example.com/post-2"/>
    <id>urn:uuid:2</id>
    <updated>{_iso(recent_b)}</updated>
    <summary>An &amp; an &amp;amp; entity test</summary>
  </entry>
</feed>
""".encode()


def _make_old_atom(old_dt: datetime) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Old Feed</title>
  <updated>{_iso(old_dt)}</updated>
  <entry>
    <title>Ancient post</title>
    <link href="https://example.com/old"/>
    <updated>{_iso(old_dt)}</updated>
  </entry>
</feed>
""".encode()


_NOW = datetime.now(timezone.utc)
SAMPLE_ATOM = _make_atom(_NOW - timedelta(days=1), _NOW - timedelta(days=2))
SAMPLE_OLD = _make_old_atom(_NOW - timedelta(days=400))


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
async def test_rss_fetcher_raises_fetch_error_on_total_failure():
    """v0.2c contract: whole-source failure raises FetchError (the old
    'return []' made an outage indistinguishable from a quiet feed —
    sources.last_error was effectively never populated)."""
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC, max_retries=1, retry_backoff=0.01)
    with respx.mock() as mock:
        mock.get("https://example.com/feed.xml").mock(
            return_value=Response(404, text="not found")
        )
        with pytest.raises(FetchError):
            await fetcher.fetch(_make_source())


@pytest.mark.asyncio
async def test_rss_fetcher_missing_url_raises_non_retryable():
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC)
    src = _make_source()
    src.url = None
    with pytest.raises(FetchError) as exc_info:
        await fetcher.fetch(src)
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_rss_fetcher_accepts_lookback_days_kwarg():
    """Regression test: `pipeline/sync.py::run_source_sync` always calls
    `fetcher.fetch(source, lookback_days=...)` — a real fetcher that
    doesn't accept that keyword raises `TypeError`, which the pipeline
    silently swallows as a per-source fetch error. This bit us for real:
    RSSFetcher used to only accept `(self, source)`, so every RSS sync
    was quietly failing. Exercise the actual production call shape here
    (not a test double) so a future signature drift fails loudly.
    """
    fetcher = RSSFetcher(timeout=FETCH_TIMEOUT_SEC)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/feed.xml").mock(
            return_value=Response(200, content=SAMPLE_ATOM)
        )
        items = await fetcher.fetch(_make_source(), lookback_days=30)
    assert len(items) == 2

    # And the override actually takes effect: a tight lookback_days that
    # excludes both entries should yield an empty list, not the default
    # constructor-time window.
    fetcher2 = RSSFetcher(timeout=FETCH_TIMEOUT_SEC, lookback_days=365)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/feed.xml").mock(
            return_value=Response(200, content=SAMPLE_ATOM)
        )
        items2 = await fetcher2.fetch(_make_source(), lookback_days=0)
    assert items2 == []
