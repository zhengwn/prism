"""arXiv fetcher — v0.2c (export.arxiv.org Atom API).

Source config via ``source.config_json``:

* ``{"categories": ["cs.AI", "cs.LG"]}`` — 分类列表，OR 连接
* ``{"query": "all:transformers"}``      — 原生 arXiv query（覆盖 categories）
* 什么都不给 → 默认 ``DEFAULT_CATEGORIES``（cs.AI / cs.LG / cs.CL）

Results are requested sorted by ``submittedDate`` descending and then
filtered by the pipeline's ``lookback_days`` window. The Atom payload
is parsed with feedparser (it understands arXiv's namespaced fields).

Per the arXiv API terms, requests to export.arxiv.org should be spaced
~3s apart — enforced globally by ``_retry.HostThrottle`` (one sync run
only makes a single request per arXiv source anyway).

Error contract: whole-source failures (endpoint dead, unparseable
response) raise ``FetchError``; a malformed single entry is skipped.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

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
from prism_sidecar.fetchers.base import FetchError, RawItem
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)

API_ENDPOINT = "https://export.arxiv.org/api/query"

DEFAULT_CATEGORIES: list[str] = ["cs.AI", "cs.LG", "cs.CL"]

_WHITESPACE_RE = re.compile(r"\s+")
_CATEGORY_RE = re.compile(r"^[a-z-]+(\.[A-Za-z-]+)?$")


def _clean(text: str) -> str:
    """arXiv 的 title/abstract 带换行 + 连续空格,压平成单行段落。"""
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def build_query_url(
    *,
    categories: list[str] | None,
    query: str | None,
    max_results: int,
) -> str:
    """Compose the export.arxiv.org query URL."""
    if query:
        search = query
    else:
        cats = categories or DEFAULT_CATEGORIES
        search = " OR ".join(f"cat:{c}" for c in cats)
    params = {
        "search_query": search,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(max_results),
    }
    return f"{API_ENDPOINT}?{urlencode(params)}"


def parse_categories(source: Source) -> list[str] | None:
    """Read + validate ``config_json.categories``. None → use defaults.

    Invalid category tokens are dropped (logged); an explicitly-set but
    entirely-invalid list is a config error → FetchError(retryable=False)
    so the user sees it in ``sources.last_error``.
    """
    cfg = source.config_json or {}
    raw = cfg.get("categories")
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [c.strip() for c in raw.split(",")]
    if not isinstance(raw, list):
        raise FetchError(
            f"config_json.categories must be a list, got {type(raw).__name__}",
            retryable=False,
        )
    valid = [str(c).strip() for c in raw if _CATEGORY_RE.match(str(c).strip())]
    dropped = len(raw) - len(valid)
    if dropped:
        log.warning(
            "[arxiv] source %s: dropped %d invalid category token(s)",
            source.id, dropped,
        )
    if not valid:
        raise FetchError(
            "config_json.categories has no valid arXiv category", retryable=False,
        )
    return valid


def _entry_authors(entry: Any) -> list[str]:
    authors = getattr(entry, "authors", None) or []
    names = []
    for a in authors:
        name = a.get("name") if isinstance(a, dict) else getattr(a, "name", None)
        if name:
            names.append(str(name))
    return names


def _entry_categories(entry: Any) -> list[str]:
    tags = getattr(entry, "tags", None) or []
    out = []
    for t in tags:
        term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
        if term:
            out.append(str(term))
    return out


def _entry_pdf_url(entry: Any) -> str | None:
    for link in getattr(entry, "links", None) or []:
        href = link.get("href") if isinstance(link, dict) else getattr(link, "href", None)
        title = link.get("title") if isinstance(link, dict) else getattr(link, "title", None)
        ltype = link.get("type") if isinstance(link, dict) else getattr(link, "type", None)
        if href and (title == "pdf" or ltype == "application/pdf"):
            return str(href)
    return None


def _arxiv_id_from(entry_id: str) -> str:
    """``http://arxiv.org/abs/2401.12345v2`` → ``2401.12345v2``."""
    return entry_id.rsplit("/abs/", 1)[-1] if "/abs/" in entry_id else entry_id


def _paper_markdown(
    *,
    title: str,
    authors: list[str],
    categories: list[str],
    abstract: str,
    pdf_url: str | None,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- 作者: {', '.join(authors) if authors else '未知'}",
        f"- 分类: {', '.join(categories) if categories else '未知'}",
    ]
    if pdf_url:
        lines.append(f"- PDF: {pdf_url}")
    lines += ["", "## 摘要", "", abstract or "（无摘要）"]
    return "\n".join(lines)


class ArxivFetcher:
    """export.arxiv.org Atom API — see module docstring."""

    kind: SourceKind = SourceKind.arxiv

    def __init__(
        self,
        timeout: float = FETCH_TIMEOUT_SEC,
        max_retries: int = FETCH_MAX_RETRIES,
        retry_backoff: float = FETCH_RETRY_BACKOFF_SEC,
        max_results: int = 50,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._max_results = max(1, min(100, max_results))

    async def fetch(
        self, source: Source, *, lookback_days: int | None = None
    ) -> list[RawItem]:
        categories = parse_categories(source)  # may raise FetchError
        cfg = source.config_json or {}
        query = cfg.get("query") if isinstance(cfg.get("query"), str) else None

        url = build_query_url(
            categories=categories, query=query, max_results=self._max_results,
        )

        try:
            body = await self._download(url)
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("[arxiv] %s (%s) fetch failed: %s", source.name, source.id, exc)
            raise FetchError(f"download failed: {exc}") from exc

        try:
            parsed = await asyncio.to_thread(feedparser.parse, body)
        except Exception as exc:  # noqa: BLE001
            raise FetchError(f"atom parse crashed: {exc}") from exc

        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise FetchError(
                f"atom unparseable: {getattr(parsed, 'bozo_exception', 'unknown')}",
            )

        lookback = lookback_days if lookback_days is not None else FETCH_LOOKBACK_DAYS
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=lookback)

        raw_items: list[RawItem] = []
        for entry in parsed.entries:
            try:
                raw = self._entry_to_raw(entry, source, now=now)
            except Exception as exc:  # noqa: BLE001
                # Malformed single entry: skip, keep going.
                log.warning("[arxiv] %s: bad entry skipped: %s", source.name, exc)
                continue
            if raw is None:
                continue
            if raw.published_at < cutoff:
                # Sorted by submittedDate descending — everything after
                # this one is older still.
                break
            raw_items.append(raw)

        log.info(
            "[arxiv] %s: %d items (of %d entries)",
            source.name, len(raw_items), len(parsed.entries),
        )
        return raw_items

    async def _download(self, url: str) -> bytes:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "PrismSidecar/0.2 (+https://github.com/zhengwn/prism)"},
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
                describe=f"[arxiv] {url}",
            )

    def _entry_to_raw(self, entry: Any, source: Source, *, now: datetime) -> RawItem | None:
        entry_id = getattr(entry, "id", None) or getattr(entry, "link", None)
        if not entry_id:
            return None

        title = _clean(getattr(entry, "title", "")) or str(entry_id)
        abstract = _clean(getattr(entry, "summary", ""))
        authors = _entry_authors(entry)
        categories = _entry_categories(entry)
        pdf_url = _entry_pdf_url(entry)

        published = getattr(entry, "published_parsed", None)
        if published is not None:
            published_at = datetime(*published[:6], tzinfo=timezone.utc)
        else:
            published_at = now

        return RawItem(
            url=str(entry_id),
            title=title,
            content=_paper_markdown(
                title=title,
                authors=authors,
                categories=categories,
                abstract=abstract,
                pdf_url=pdf_url,
            )[:8000],
            published_at=published_at,
            author=", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "") if authors else None,
            content_type=ContentType.paper,
            metadata={
                "source_name": source.name,
                "feed_kind": "arxiv",
                "arxiv_id": _arxiv_id_from(str(entry_id)),
                "categories": categories,
                "pdf_url": pdf_url,
            },
        )


__all__ = ["ArxivFetcher", "DEFAULT_CATEGORIES", "build_query_url", "parse_categories"]
