"""Generic RSS / Atom fetcher.

Uses httpx for the HTTP fetch and feedparser for parsing. Items older than
`FETCH_LOOKBACK_DAYS` are dropped to avoid pulling years of history on the
first run.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx

from prism_sidecar.config import (
    FETCH_LOOKBACK_DAYS,
    FETCH_MAX_RETRIES,
    FETCH_RETRY_BACKOFF_SEC,
    FETCH_TIMEOUT_SEC,
)
from prism_sidecar.fetchers.base import Fetcher, RawItem
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """Naive HTML → text. Good enough for v0.2a; v0.2b will use BeautifulSoup."""
    if not text:
        return ""
    cleaned = _HTML_COMMENT_RE.sub("", text)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    # Common entities. Not exhaustive — just the frequent ones.
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _parse_published(entry: Any, fallback: datetime) -> datetime:
    """Extract a datetime from a feed entry, falling back to `fallback`."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    for key in ("published", "updated", "created"):
        raw = getattr(entry, key, None)
        if raw:
            try:
                # feedparser provides *_parsed when it can; here we try
                # dateutil as a backstop.
                from dateutil import parser as du

                dt = du.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ImportError, ValueError, TypeError):
                continue
    return fallback


def _entry_link(entry: Any) -> str | None:
    """Pick the canonical link from a feed entry, handling Atom/RSS quirks."""
    link = getattr(entry, "link", None)
    if link:
        return link
    links = getattr(entry, "links", None) or []
    for cand in links:
        href = cand.get("href") if isinstance(cand, dict) else None
        if href:
            return href
    return None


def _entry_author(entry: Any) -> str | None:
    author = getattr(entry, "author", None)
    if author:
        return author
    dc_author = getattr(entry, "dc_creator", None)
    if dc_author:
        return dc_author
    return None


def _entry_content(entry: Any) -> str:
    """Extract entry body. Prefers `content` over `summary`."""
    if getattr(entry, "content", None):
        # content is a list of dicts; first wins.
        try:
            return entry.content[0].get("value", "") or ""
        except (IndexError, AttributeError, TypeError):
            pass
    return getattr(entry, "summary", "") or ""


class RSSFetcher:
    """Generic RSS / Atom fetcher."""

    kind: SourceKind = SourceKind.rss

    def __init__(
        self,
        timeout: float = FETCH_TIMEOUT_SEC,
        max_retries: int = FETCH_MAX_RETRIES,
        retry_backoff: float = FETCH_RETRY_BACKOFF_SEC,
        lookback_days: int = FETCH_LOOKBACK_DAYS,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._lookback = timedelta(days=lookback_days)

    async def _download(self, url: str) -> bytes:
        """GET with retry. Returns body bytes."""
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "PrismSidecar/0.2 (+https://github.com/zhengwn/prism)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
        ) as client:
            for attempt in range(1, self._max_retries + 2):  # retries + 1
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    return resp.content
                except (httpx.HTTPError, httpx.StreamError) as exc:
                    last_exc = exc
                    if attempt > self._max_retries:
                        break
                    backoff = self._retry_backoff * (2 ** (attempt - 1))
                    log.warning(
                        "[rss] fetch %s failed (attempt %d/%d): %s — retry in %.1fs",
                        url, attempt, self._max_retries + 1, exc, backoff,
                    )
                    await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    async def fetch(self, source: Source) -> list[RawItem]:
        if not source.url:
            log.warning("[rss] source %s has no url; skipping", source.id)
            return []

        try:
            body = await self._download(source.url)
        except Exception as exc:
            log.error("[rss] %s (%s) fetch failed: %s", source.name, source.id, exc)
            return []

        # feedparser is sync, but cheap; run in a thread to avoid blocking
        # the loop.
        try:
            parsed = await asyncio.to_thread(feedparser.parse, body)
        except Exception as exc:
            log.error("[rss] %s feedparser error: %s", source.name, exc)
            return []

        if getattr(parsed, "bozo", False) and not parsed.entries:
            log.warning(
                "[rss] %s feed parse failed: %s",
                source.name,
                getattr(parsed, "bozo_exception", "unknown"),
            )
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - self._lookback
        raw_items: list[RawItem] = []

        for entry in parsed.entries:
            link = _entry_link(entry)
            if not link:
                continue

            published_at = _parse_published(entry, fallback=now)
            if published_at < cutoff:
                # Older than the lookback window — skip.
                continue

            title = (getattr(entry, "title", "") or "").strip() or link
            body_text = _strip_html(_entry_content(entry))
            if not body_text:
                body_text = title  # worst case: LLM has the title to work with

            raw_items.append(
                RawItem(
                    url=link,
                    title=title,
                    content=body_text[:8000],  # bound the prompt payload
                    published_at=published_at,
                    author=_entry_author(entry),
                    content_type=ContentType.article,
                    metadata={"source_name": source.name, "feed_kind": "rss"},
                )
            )

        log.info("[rss] %s: %d items (of %d entries)", source.name, len(raw_items), len(parsed.entries))
        return raw_items


__all__ = ["RSSFetcher", "_strip_html"]
