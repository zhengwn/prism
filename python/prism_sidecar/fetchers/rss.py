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
from prism_sidecar.fetchers import _retry
from prism_sidecar.fetchers._retry import retry_async
from prism_sidecar.fetchers.base import FetchError, Fetcher, RawItem
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
        """GET with retry (shared `retry_async` helper). Returns body bytes."""
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "PrismSidecar/0.2 (+https://github.com/zhengwn/prism)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
        ) as client:

            async def _get() -> bytes:
                await _retry.throttle.wait(url)
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content

            return await retry_async(
                _get,
                max_retries=self._max_retries,
                backoff_base=self._retry_backoff,
                describe=f"[rss] {url}",
            )

    async def fetch(
        self, source: Source, *, lookback_days: int | None = None
    ) -> list[RawItem]:
        # v0.2c contract: whole-source failures raise FetchError so the
        # pipeline can record sources.last_error (previously we returned
        # [] and the outage was indistinguishable from a quiet feed).
        if not source.url:
            raise FetchError(
                f"source {source.id} has no url", retryable=False,
            )

        try:
            body = await self._download(source.url)
        except FetchError:
            raise
        except Exception as exc:
            log.error("[rss] %s (%s) fetch failed: %s", source.name, source.id, exc)
            raise FetchError(f"download failed: {exc}") from exc

        # feedparser is sync, but cheap; run in a thread to avoid blocking
        # the loop.
        try:
            parsed = await asyncio.to_thread(feedparser.parse, body)
        except Exception as exc:
            log.error("[rss] %s feedparser error: %s", source.name, exc)
            raise FetchError(f"feed parse crashed: {exc}") from exc

        if getattr(parsed, "bozo", False) and not parsed.entries:
            bozo = getattr(parsed, "bozo_exception", "unknown")
            log.warning("[rss] %s feed parse failed: %s", source.name, bozo)
            raise FetchError(f"feed unparseable: {bozo}")

        lookback = (
            timedelta(days=lookback_days) if lookback_days is not None else self._lookback
        )
        now = datetime.now(timezone.utc)
        cutoff = now - lookback
        raw_items: list[RawItem] = []

        for entry in parsed.entries:
            link = _entry_link(entry)
            if not link:
                continue

            published_at = _parse_published(entry, fallback=now)
            if published_at < cutoff:
                # Older than the lookback window — skip.
                continue

            raw = self._entry_to_raw(entry, source, link=link, published_at=published_at)
            if raw is not None:
                raw_items.append(raw)

        log.info("[rss] %s: %d items (of %d entries)", source.name, len(raw_items), len(parsed.entries))
        return raw_items

    def _entry_to_raw(
        self,
        entry: Any,
        source: Source,
        *,
        link: str,
        published_at: datetime,
    ) -> RawItem | None:
        """Build one RawItem from a feedparser entry.

        Extracted as a hook (v0.2c) so RSS-family fetchers — Podcast is
        the first — can subclass RSSFetcher and enrich the item
        (enclosure, duration, …) without duplicating the download /
        parse / lookback plumbing in `fetch`.
        """
        title = (getattr(entry, "title", "") or "").strip() or link
        body_text = _strip_html(_entry_content(entry))
        if not body_text:
            body_text = title  # worst case: LLM has the title to work with

        return RawItem(
            url=link,
            title=title,
            content=body_text[:8000],  # bound the prompt payload
            published_at=published_at,
            author=_entry_author(entry),
            content_type=ContentType.article,
            metadata={"source_name": source.name, "feed_kind": "rss"},
        )


__all__ = ["RSSFetcher", "_strip_html"]
