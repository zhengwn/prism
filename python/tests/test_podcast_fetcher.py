"""Offline tests for the Podcast fetcher (RSS + iTunes extensions)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import respx
from httpx import Response

from prism_sidecar.fetchers.base import FetchError
from prism_sidecar.fetchers.podcast import PodcastFetcher, parse_itunes_duration
from prism_sidecar.models import ContentType, Source, SourceKind

_NOW = datetime.now(timezone.utc)
FEED = "https://pod.example.com/feed.xml"


def _make_source() -> Source:
    return Source(
        id="src_pod_test",
        name="Pod Test",
        kind=SourceKind.podcast,
        url=FEED,
        enabled=True,
    )


def _rfc822(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _feed(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Pod</title>
    <link>https://pod.example.com</link>
    {items}
  </channel>
</rss>""".encode()


def _episode(
    slug: str,
    *,
    published: datetime,
    duration: str = "01:02:05",
    enclosure: str | None = "https://cdn.example.com/{slug}.mp3",
    episode_no: str | None = "42",
) -> str:
    enc = ""
    if enclosure:
        enc = f'<enclosure url="{enclosure.format(slug=slug)}" length="1234" type="audio/mpeg"/>'
    ep = f"<itunes:episode>{episode_no}</itunes:episode>" if episode_no else ""
    return f"""<item>
      <title>Episode {slug}</title>
      <link>https://pod.example.com/ep/{slug}</link>
      <guid>https://pod.example.com/ep/{slug}</guid>
      <pubDate>{_rfc822(published)}</pubDate>
      <description>Show notes for {slug} with &lt;b&gt;bold&lt;/b&gt; text.</description>
      <itunes:duration>{duration}</itunes:duration>
      {ep}
      {enc}
    </item>"""


def test_parse_itunes_duration():
    assert parse_itunes_duration("01:02:05") == 3725
    assert parse_itunes_duration("62:05") == 3725
    assert parse_itunes_duration("3725") == 3725
    assert parse_itunes_duration(3725) == 3725
    assert parse_itunes_duration("") is None
    assert parse_itunes_duration(None) is None
    assert parse_itunes_duration("1:2:3:4") is None
    assert parse_itunes_duration("abc") is None


@pytest.mark.asyncio
async def test_fetch_builds_audio_rawitems():
    fetcher = PodcastFetcher(max_retries=0)
    body = _feed(_episode("alpha", published=_NOW - timedelta(days=1)))
    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_make_source(), lookback_days=7)

    assert len(items) == 1
    raw = items[0]
    assert raw.url == "https://pod.example.com/ep/alpha"
    assert raw.content_type == ContentType.audio
    assert raw.duration_sec == 3725
    assert raw.metadata["feed_kind"] == "podcast"
    assert raw.metadata["audio_url"] == "https://cdn.example.com/alpha.mp3"
    assert raw.metadata["audio_type"] == "audio/mpeg"
    assert raw.metadata["episode"] == "42"
    # Show notes made it through the shared HTML-strip path.
    assert "Show notes for alpha" in raw.content
    assert "<b>" not in raw.content


@pytest.mark.asyncio
async def test_fetch_keeps_entry_without_enclosure():
    fetcher = PodcastFetcher(max_retries=0)
    body = _feed(_episode("teaser", published=_NOW - timedelta(days=1), enclosure=None))
    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_make_source(), lookback_days=7)
    assert len(items) == 1
    assert "audio_url" not in items[0].metadata


@pytest.mark.asyncio
async def test_fetch_inherits_lookback_and_error_contract():
    """继承自 RSSFetcher:lookback 过滤 + 整源失败 raise FetchError。"""
    fetcher = PodcastFetcher(max_retries=0)
    body = _feed(_episode("old", published=_NOW - timedelta(days=400)))
    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_make_source(), lookback_days=7)
    assert items == []

    with respx.mock() as mock:
        mock.get(FEED).mock(return_value=Response(500, text="boom"))
        with pytest.raises(FetchError):
            await fetcher.fetch(_make_source(), lookback_days=7)
