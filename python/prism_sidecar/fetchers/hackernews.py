"""Hacker News fetcher — backed by the Algolia search API.

HN has no real RSS; we use the Algolia search endpoint with a curated list
of AI-related keywords, then de-duplicate by `objectID`. For each hit, the
`content` we hand to the distiller is a small synthesis: the title, the
URL, and (if present) the `story_text`. The LLM knows to treat HN items
as link posts and produce a useful zh summary.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from prism_sidecar.config import FETCH_MAX_RETRIES, FETCH_RETRY_BACKOFF_SEC, FETCH_TIMEOUT_SEC
from prism_sidecar.fetchers import _retry
from prism_sidecar.fetchers._retry import retry_async
from prism_sidecar.fetchers.base import FetchError, Fetcher, RawItem
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)


# Default keyword pool. Each keyword is queried once and results are merged
# + de-duplicated by objectID.
DEFAULT_KEYWORDS: list[str] = [
    "AI",
    "LLM",
    "GPT",
    "Claude",
    "agent",
    "machine learning",
]


ALGOLIA_ENDPOINT = "https://hn.algolia.com/api/v1/search"


def _parse_hn_date(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        # HN returns "2024-05-22T12:34:56Z"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _hit_content(hit: dict[str, Any]) -> str:
    """Synthesize a content string for the distiller.

    HN posts are link posts — there's no real body. We assemble what we
    have so the LLM has something concrete to work with.
    """
    parts: list[str] = []
    title = hit.get("title") or hit.get("story_title")
    url = hit.get("url") or hit.get("story_url")
    story_text = hit.get("story_text")
    if title:
        parts.append(f"Title: {title}")
    if url:
        parts.append(f"URL: {url}")
    if story_text:
        parts.append(f"\nDiscussion excerpt:\n{story_text}")
    if not parts:
        parts.append("(no content)")
    return "\n".join(parts)


class HackerNewsFetcher:
    """Algolia-backed HN fetcher."""

    # This fetcher is invoked explicitly by the pipeline; we don't register
    # it in the kind registry by default. The fixture source for HN is
    # `kind=rss` with `config_json={"is_hn_algolia": true, "tags": "AI"}`.
    kind: SourceKind = SourceKind.rss

    def __init__(
        self,
        keywords: list[str] | None = None,
        hits_per_page: int = 20,
        timeout: float = FETCH_TIMEOUT_SEC,
        max_retries: int = FETCH_MAX_RETRIES,
        retry_backoff: float = FETCH_RETRY_BACKOFF_SEC,
    ) -> None:
        self._keywords = list(keywords) if keywords else list(DEFAULT_KEYWORDS)
        self._hits_per_page = hits_per_page
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def _search_once(
        self,
        client: httpx.AsyncClient,
        keyword: str,
    ) -> list[dict[str, Any]]:
        """One keyword query with retry. Raises on unrecoverable failure —
        the caller decides whether losing SOME keywords is fatal."""
        params = {
            "tags": "story",
            "query": keyword,
            "hitsPerPage": str(self._hits_per_page),
        }

        async def _get() -> list[dict[str, Any]]:
            await _retry.throttle.wait(ALGOLIA_ENDPOINT)
            resp = await client.get(ALGOLIA_ENDPOINT, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("hits", [])

        return await retry_async(
            _get,
            max_retries=self._max_retries,
            backoff_base=self._retry_backoff,
            describe=f"[hn] query={keyword!r}",
        )

    async def fetch(
        self, source: Source, *, lookback_days: int | None = None
    ) -> list[RawItem]:
        # `lookback_days` is accepted (not just swallowed via **kwargs, so
        # the signature stays self-documenting) but intentionally unused:
        # HN has no per-item lookback concept here — v0.2a fetches
        # all-time per keyword and relies on the url dedup in the store to
        # avoid re-inserting old stories. See the module docstring.
        # Use keywords from config if provided, else default pool.
        cfg = source.config_json or {}
        keywords = cfg.get("keywords")
        if isinstance(keywords, list) and keywords:
            queries = [str(k) for k in keywords]
        elif isinstance(keywords, str) and keywords.strip():
            queries = [keywords.strip()]
        else:
            queries = list(self._keywords)

        # Per-source rate limit + lookback window? v0.2a fetches all-time for
        # HN; the de-dup on items.url keeps it from blowing up.

        merged: dict[str, dict[str, Any]] = {}
        failed: list[tuple[str, Exception]] = []
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "PrismSidecar/0.2 (+https://github.com/zhengwn/prism)"},
        ) as client:
            for kw in queries:
                # Per-keyword failures are "per-item" under the v0.2c
                # contract — skip and continue. Only ALL keywords failing
                # means the source (Algolia) is actually down.
                try:
                    hits = await self._search_once(client, kw)
                except Exception as exc:  # noqa: BLE001
                    log.error("[hn] query=%r gave up: %s", kw, exc)
                    failed.append((kw, exc))
                    continue
                for hit in hits:
                    oid = hit.get("objectID")
                    if not oid:
                        continue
                    if oid not in merged:
                        merged[oid] = hit
                # Be polite to Algolia (the throttle already spaces
                # requests; this keeps the historical extra pause).
                await asyncio.sleep(0.2)

        if failed and len(failed) == len(queries):
            raise FetchError(
                f"all {len(queries)} Algolia queries failed "
                f"(first: {failed[0][1]})",
            ) from failed[0][1]

        raw_items: list[RawItem] = []
        for hit in merged.values():
            url = hit.get("url") or hit.get("story_url")
            title = hit.get("title") or hit.get("story_title")
            if not url or not title:
                # If the hit is a comment / job, skip — we only want stories.
                continue

            raw_items.append(
                RawItem(
                    url=str(url),
                    title=str(title),
                    content=_hit_content(hit),
                    published_at=_parse_hn_date(hit.get("created_at")),
                    author=hit.get("author"),
                    content_type=ContentType.post,
                    metadata={"hn_object_id": hit.get("objectID"), "hn_points": hit.get("points")},
                )
            )

        # Newest first
        raw_items.sort(key=lambda r: r.published_at, reverse=True)
        log.info("[hn] %s: %d unique hits (of %d queries)", source.name, len(raw_items), len(queries))
        return raw_items


def is_hn_source(source: Source) -> bool:
    """True if the given source should be handled by HackerNewsFetcher.

    Detection: `config_json.is_hn_algolia == True`, OR url hosts `hn.algolia.com`.
    """
    cfg = source.config_json or {}
    if cfg.get("is_hn_algolia") is True:
        return True
    return "hn.algolia.com" in (source.url or "")


__all__ = ["HackerNewsFetcher", "is_hn_source", "DEFAULT_KEYWORDS"]
