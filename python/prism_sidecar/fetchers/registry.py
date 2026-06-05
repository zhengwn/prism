"""Fetcher registry — maps SourceKind to the default Fetcher instance.

The HN fetcher is special: it lives at the same SourceKind.rss slot, but
the pipeline detects it via `source.config_json.is_hn_algolia` and calls
HackerNewsFetcher directly. The default for `rss` is RSSFetcher.
"""

from __future__ import annotations

from prism_sidecar.fetchers.base import Fetcher
from prism_sidecar.fetchers.hackernews import HackerNewsFetcher
from prism_sidecar.fetchers.rss import RSSFetcher
from prism_sidecar.models import SourceKind

_REGISTRY: dict[SourceKind, Fetcher] = {
    SourceKind.rss: RSSFetcher(),
    # Other SourceKinds (youtube, podcast, blog, x, pdf, file) are not
    # implemented in v0.2a. The pipeline treats them as no-op and records
    # an error.
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

    async def fetch(self, source):  # type: ignore[no-untyped-def]
        import logging

        logging.getLogger(__name__).warning(
            "[fetcher] no fetcher registered for kind=%s (source=%s); skipping",
            source.kind, source.id,
        )
        return []


_NOOP_FETCHER: Fetcher = _NoopFetcher()  # type: ignore[assignment]


__all__ = ["get_fetcher"]
