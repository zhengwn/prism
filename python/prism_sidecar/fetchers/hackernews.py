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
from prism_sidecar.fetchers.base import Fetcher, RawItem
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
        params = {
            "tags": "story",
            "query": keyword,
            "hitsPerPage": str(self._hits_per_page),
        }
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                resp = await client.get(ALGOLIA_ENDPOINT, params=params)
                resp.raise_for_status()
                data = resp.json()
                return data.get("hits", [])
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt > self._max_retries:
                    break
                backoff = self._retry_backoff * (2 ** (attempt - 1))
                log.warning(
                    "[hn] query=%r failed (attempt %d/%d): %s — retry in %.1fs",
                    keyword, attempt, self._max_retries + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
        if last_exc:
            log.error("[hn] query=%r gave up: %s", keyword, last_exc)
        return []

    async def fetch(self, source: Source) -> list[RawItem]:
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
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "PrismSidecar/0.2 (+https://github.com/zhengwn/prism)"},
        ) as client:
            for kw in queries:
                hits = await self._search_once(client, kw)
                for hit in hits:
                    oid = hit.get("objectID")
                    if not oid:
                        continue
                    if oid not in merged:
                        merged[oid] = hit
                # Be polite to Algolia.
                await asyncio.sleep(0.2)

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
