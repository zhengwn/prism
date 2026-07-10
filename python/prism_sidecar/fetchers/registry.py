"""Fetcher registry — maps SourceKind to the default Fetcher instance.

The HN fetcher is special: it lives at the same SourceKind.rss slot, but
the pipeline detects it via `source.config_json.is_hn_algolia` and calls
HackerNewsFetcher directly. The default for `rss` is RSSFetcher.
"""

from __future__ import annotations

from prism_sidecar.fetchers.arxiv import ArxivFetcher
from prism_sidecar.fetchers.base import Fetcher
from prism_sidecar.fetchers.bilibili import BilibiliFetcher
from prism_sidecar.fetchers.hackernews import HackerNewsFetcher
from prism_sidecar.fetchers.podcast import PodcastFetcher
from prism_sidecar.fetchers.rss import RSSFetcher
from prism_sidecar.fetchers.x import XFetcher
from prism_sidecar.fetchers.youtube import YouTubeFetcher
from prism_sidecar.models import SourceKind

_REGISTRY: dict[SourceKind, Fetcher] = {
    SourceKind.rss: RSSFetcher(),
    SourceKind.bilibili: BilibiliFetcher(),
    SourceKind.youtube: YouTubeFetcher(),
    SourceKind.podcast: PodcastFetcher(),
    SourceKind.arxiv: ArxivFetcher(),
    SourceKind.x: XFetcher(),
    # `blog` intentionally routes to RSSFetcher too — a blog source is
    # just an RSS feed with a different label in the UI.
    SourceKind.blog: RSSFetcher(),
    # Remaining SourceKinds (pdf, file) are not implemented yet
    # (v0.2c backlog). The pipeline treats them as no-op.
}

_HN_FETCHER: Fetcher = HackerNewsFetcher()


def get_fetcher(source: "Source") -> Fetcher:  # noqa: F821
    """Return the appropriate fetcher for a given source.

    For `rss` sources, the HN fetcher takes priority if the source has
    `config_json.is_hn_algolia=True` or its URL points at hn.algolia.com.
    """
    from prism_sidecar.fetchers.hackernews import is_hn_source

    if source.kind == SourceKind.rss and is_hn_source(source):
        return _HN_FETCHER
    return _REGISTRY.get(source.kind, _NOOP_FETCHER)


class _NoopFetcher:
    """Placeholder for unimplemented SourceKinds. Returns empty list."""

    kind: SourceKind = SourceKind.blog  # arbitrary

    async def fetch(self, source, **_kwargs):  # type: ignore[no-untyped-def]
        # Accepts (and ignores) `lookback_days` — the pipeline passes it
        # to every fetcher unconditionally, see `Fetcher.fetch`'s docstring
        # in fetchers/base.py.
        import logging

        logging.getLogger(__name__).warning(
            "[fetcher] no fetcher registered for kind=%s (source=%s); skipping",
            source.kind, source.id,
        )
        return []


_NOOP_FETCHER: Fetcher = _NoopFetcher()  # type: ignore[assignment]


__all__ = ["get_fetcher"]
