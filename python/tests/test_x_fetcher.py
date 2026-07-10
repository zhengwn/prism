"""Offline tests for the X (Twitter) fetcher — bridge-RSS PoC.

Covers the X-specific surface (handle/URL parsing, feed-URL resolution,
tweet metadata enrichment, config-error contract) without hitting the
network. The download/parse/lookback plumbing is RSSFetcher's and is
tested in test_rss_fetcher.py; here we mock the resolved bridge feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import respx
from httpx import Response

from prism_sidecar.distillers.base import _build_prompt, _is_x
from prism_sidecar.fetchers.base import FetchError, RawItem
from prism_sidecar.fetchers.x import (
    XFetcher,
    extract_handle,
    parse_status_link,
    resolve_feed_url,
)
from prism_sidecar.models import ContentType, Source, SourceKind

_NOW = datetime.now(timezone.utc)
BRIDGE = "https://rsshub.example.com"
FEED = f"{BRIDGE}/twitter/user/simonw"


def _source(url: str = "@simonw", config_json=None) -> Source:
    return Source(
        id="src_x_test",
        name="Simon on X",
        kind=SourceKind.x,
        url=url,
        config_json=config_json or {},
    )


def _rfc822(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _feed(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>simonw twitter timeline</title>
    <link>https://x.com/simonw</link>
    {items}
  </channel>
</rss>""".encode()


def _tweet(tweet_id: str, *, title: str, published: datetime) -> str:
    return f"""<item>
      <title>{title}</title>
      <link>https://x.com/simonw/status/{tweet_id}</link>
      <guid>https://x.com/simonw/status/{tweet_id}</guid>
      <pubDate>{_rfc822(published)}</pubDate>
      <description>{title}</description>
    </item>"""


# ----- extract_handle ------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@simonw", "simonw"),
        ("simonw", "simonw"),
        ("https://x.com/simonw", "simonw"),
        ("https://twitter.com/simonw/", "simonw"),
        ("x.com/simonw/status/123", "simonw"),
        ("https://nitter.net/simonw", "simonw"),
        ("https://x.com/home", None),        # reserved route, not a handle
        ("https://x.com/", None),            # no handle
        ("this is not a handle at all", None),
        ("", None),
    ],
)
def test_extract_handle(raw, expected):
    assert extract_handle(raw) == expected


def test_extract_handle_rejects_overlong():
    # X caps handles at 15 chars.
    assert extract_handle("a" * 16) is None


# ----- resolve_feed_url ----------------------------------------------------

def test_resolve_feed_url_prefers_explicit_feed_url():
    src = _source(url="@simonw", config_json={"feed_url": "https://n.example/x.rss", "bridge": BRIDGE})
    assert resolve_feed_url(src) == "https://n.example/x.rss"


def test_resolve_feed_url_direct_feed_url_in_url_field():
    src = _source(url="https://rsshub.example.com/twitter/user/simonw")
    assert resolve_feed_url(src) == "https://rsshub.example.com/twitter/user/simonw"


def test_resolve_feed_url_builds_from_handle_and_bridge():
    src = _source(url="@simonw", config_json={"bridge": BRIDGE + "/"})
    assert resolve_feed_url(src) == FEED


def test_resolve_feed_url_handle_without_bridge_is_config_error():
    src = _source(url="@simonw")  # no bridge, no feed_url
    with pytest.raises(FetchError) as ei:
        resolve_feed_url(src)
    assert ei.value.retryable is False


def test_resolve_feed_url_unparseable_is_config_error():
    src = _source(url="https://x.com/home", config_json={"bridge": BRIDGE})
    with pytest.raises(FetchError) as ei:
        resolve_feed_url(src)
    assert ei.value.retryable is False


# ----- parse_status_link ---------------------------------------------------

def test_parse_status_link():
    assert parse_status_link("https://x.com/simonw/status/1789") == ("simonw", "1789")
    assert parse_status_link("https://nitter.net/simonw/status/42#m") == ("simonw", "42")
    assert parse_status_link("https://x.com/simonw") == (None, None)
    assert parse_status_link("") == (None, None)


# ----- fetch (mocked bridge feed) ------------------------------------------

@pytest.mark.asyncio
async def test_fetch_enriches_tweet_metadata():
    fetcher = XFetcher(max_retries=0)
    body = _feed(_tweet("1789", title="Shipping a new LLM tool today", published=_NOW - timedelta(hours=2)))
    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_source(config_json={"bridge": BRIDGE}), lookback_days=7)

    assert len(items) == 1
    raw = items[0]
    assert raw.content_type == ContentType.post
    assert raw.metadata["feed_kind"] == "x"
    assert raw.metadata["handle"] == "simonw"
    assert raw.metadata["tweet_id"] == "1789"
    assert raw.metadata["tweet_kind"] == "post"


@pytest.mark.asyncio
async def test_fetch_classifies_retweet():
    fetcher = XFetcher(max_retries=0)
    body = _feed(_tweet("55", title="RT by @simonw: big news", published=_NOW - timedelta(hours=1)))
    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_source(config_json={"bridge": BRIDGE}), lookback_days=7)
    assert items[0].metadata["tweet_kind"] == "retweet"


@pytest.mark.asyncio
async def test_fetch_inherits_lookback_and_error_contract():
    """继承自 RSSFetcher:lookback 过滤 + 整源失败 raise FetchError。"""
    fetcher = XFetcher(max_retries=0)
    old = _feed(_tweet("1", title="ancient tweet", published=_NOW - timedelta(days=400)))
    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(200, content=old))
        items = await fetcher.fetch(_source(config_json={"bridge": BRIDGE}), lookback_days=7)
    assert items == []

    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(500, text="boom"))
        with pytest.raises(FetchError):
            await fetcher.fetch(_source(config_json={"bridge": BRIDGE}), lookback_days=7)


@pytest.mark.asyncio
async def test_fetch_config_error_raises_before_network():
    """No bridge → FetchError(retryable=False) without any HTTP call."""
    fetcher = XFetcher(max_retries=0)
    with pytest.raises(FetchError) as ei:
        await fetcher.fetch(_source(url="@simonw"), lookback_days=7)
    assert ei.value.retryable is False


# ----- distill prompt routing ----------------------------------------------

def test_x_items_use_short_form_prompt():
    raw = RawItem(
        url="https://x.com/simonw/status/1",
        title="a terse tweet",
        content="Just shipped something cool. https://example.com",
        published_at=_NOW,
        author="simonw",
        content_type=ContentType.post,
        metadata={"feed_kind": "x", "handle": "simonw"},
    )
    assert _is_x(raw) is True
    prompt = _build_prompt(raw)
    assert "X（推特）" in prompt
    assert "thread" in prompt.lower()
    # The tweet body is embedded in the prompt.
    assert "Just shipped something cool" in prompt


def test_non_x_items_use_general_prompt():
    raw = RawItem(
        url="https://blog.example.com/post",
        title="A long article",
        content="Some article body." * 5,
        published_at=_NOW,
        metadata={"feed_kind": "rss"},
    )
    assert _is_x(raw) is False
    prompt = _build_prompt(raw)
    assert "X（推特）" not in prompt
