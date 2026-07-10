"""Test `fetchers/registry.py`'s dispatch logic.

Previously untested: `get_fetcher`'s SourceKind → Fetcher mapping (including
the HN-over-RSS special case) and the `_NoopFetcher` fallback for
unimplemented kinds (pdf / file — x/youtube/podcast/blog are all wired now).
"""

from __future__ import annotations

import pytest

from prism_sidecar.fetchers.arxiv import ArxivFetcher
from prism_sidecar.fetchers.bilibili import BilibiliFetcher
from prism_sidecar.fetchers.hackernews import HackerNewsFetcher
from prism_sidecar.fetchers.podcast import PodcastFetcher
from prism_sidecar.fetchers.registry import get_fetcher
from prism_sidecar.fetchers.rss import RSSFetcher
from prism_sidecar.fetchers.x import XFetcher
from prism_sidecar.fetchers.youtube import YouTubeFetcher
from prism_sidecar.models import Source, SourceKind


def _source(kind: SourceKind, url: str = "https://example.com/feed.xml", config_json=None) -> Source:
    return Source(
        id="src_test",
        name="Test",
        kind=kind,
        url=url,
        config_json=config_json or {},
    )


def test_rss_source_gets_rss_fetcher():
    fetcher = get_fetcher(_source(SourceKind.rss))
    assert isinstance(fetcher, RSSFetcher)


def test_hn_flagged_rss_source_gets_hn_fetcher():
    src = _source(SourceKind.rss, config_json={"is_hn_algolia": True})
    fetcher = get_fetcher(src)
    assert isinstance(fetcher, HackerNewsFetcher)


def test_hn_url_rss_source_gets_hn_fetcher():
    src = _source(SourceKind.rss, url="https://hn.algolia.com/api/v1/search")
    fetcher = get_fetcher(src)
    assert isinstance(fetcher, HackerNewsFetcher)


def test_bilibili_source_gets_bilibili_fetcher():
    fetcher = get_fetcher(_source(SourceKind.bilibili, config_json={"bvid": "BV1xx"}))
    assert isinstance(fetcher, BilibiliFetcher)


def test_youtube_source_gets_youtube_fetcher():
    fetcher = get_fetcher(_source(SourceKind.youtube, config_json={"video": "dQw4w9WgXcQ"}))
    assert isinstance(fetcher, YouTubeFetcher)


def test_podcast_source_gets_podcast_fetcher():
    fetcher = get_fetcher(_source(SourceKind.podcast))
    assert isinstance(fetcher, PodcastFetcher)
    # PodcastFetcher extends RSSFetcher — the isinstance order matters
    # here: a podcast source must get the SUBCLASS, and a plain rss
    # source must NOT accidentally get a PodcastFetcher.
    assert isinstance(fetcher, RSSFetcher)
    assert not isinstance(get_fetcher(_source(SourceKind.rss)), PodcastFetcher)


def test_arxiv_source_gets_arxiv_fetcher():
    fetcher = get_fetcher(_source(SourceKind.arxiv, config_json={"categories": ["cs.AI"]}))
    assert isinstance(fetcher, ArxivFetcher)


def test_blog_source_routes_to_rss_fetcher():
    fetcher = get_fetcher(_source(SourceKind.blog))
    assert isinstance(fetcher, RSSFetcher)
    assert not isinstance(fetcher, PodcastFetcher)


def test_x_source_gets_x_fetcher():
    fetcher = get_fetcher(_source(SourceKind.x))
    assert isinstance(fetcher, XFetcher)
    # XFetcher extends RSSFetcher (bridge-RSS PoC), so the same
    # subclass-ordering caveat as PodcastFetcher applies.
    assert isinstance(fetcher, RSSFetcher)
    assert not isinstance(get_fetcher(_source(SourceKind.rss)), XFetcher)


@pytest.mark.parametrize(
    "kind",
    [SourceKind.pdf, SourceKind.file],
)
@pytest.mark.asyncio
async def test_unimplemented_kinds_get_noop_fetcher(kind):
    fetcher = get_fetcher(_source(kind))
    # Must accept the pipeline's real call shape (source + lookback_days
    # kwarg) without raising, and return an empty list rather than None.
    items = await fetcher.fetch(_source(kind), lookback_days=7)
    assert items == []
