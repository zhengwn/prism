"""Offline tests for the arXiv fetcher — Atom payloads mocked via respx."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import respx
from httpx import Response

from prism_sidecar.fetchers.arxiv import (
    ArxivFetcher,
    build_query_url,
    parse_categories,
)
from prism_sidecar.fetchers.base import FetchError
from prism_sidecar.models import ContentType, Source, SourceKind

_NOW = datetime.now(timezone.utc)


def _make_source(config_json: dict | None = None) -> Source:
    return Source(
        id="src_arxiv_test",
        name="arXiv Test",
        kind=SourceKind.arxiv,
        url="https://export.arxiv.org/api/query",
        enabled=True,
        config_json=config_json or {},
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atom(entries: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query Results</title>
  <updated>{_iso(_NOW)}</updated>
  {entries}
</feed>""".encode()


def _entry(
    arxiv_id: str,
    *,
    title: str,
    published: datetime,
    abstract: str = "We propose a method.",
) -> str:
    return f"""<entry>
    <id>http://arxiv.org/abs/{arxiv_id}</id>
    <title>{title}</title>
    <summary> {abstract}
      Multi-line   abstract text. </summary>
    <published>{_iso(published)}</published>
    <updated>{_iso(published)}</updated>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link href="http://arxiv.org/abs/{arxiv_id}" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/{arxiv_id}" rel="related" type="application/pdf"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
  </entry>"""


API = "https://export.arxiv.org/api/query"


# ---- pure helpers ----------------------------------------------------------


def test_build_query_url_defaults():
    url = build_query_url(categories=None, query=None, max_results=50)
    assert "cat%3Acs.AI+OR+cat%3Acs.LG+OR+cat%3Acs.CL" in url
    assert "sortBy=submittedDate" in url
    assert "sortOrder=descending" in url
    assert "max_results=50" in url


def test_build_query_url_explicit_query_wins():
    url = build_query_url(categories=["cs.AI"], query="all:transformers", max_results=10)
    assert "all%3Atransformers" in url
    assert "cs.AI" not in url


def test_parse_categories():
    assert parse_categories(_make_source()) is None  # defaults apply
    assert parse_categories(_make_source({"categories": ["cs.AI", "stat.ML"]})) == [
        "cs.AI", "stat.ML",
    ]
    # Comma-separated string form is accepted too.
    assert parse_categories(_make_source({"categories": "cs.AI, cs.CL"})) == [
        "cs.AI", "cs.CL",
    ]
    # Invalid tokens dropped; all-invalid raises non-retryable.
    assert parse_categories(_make_source({"categories": ["cs.AI", "DROP TABLE"]})) == ["cs.AI"]
    with pytest.raises(FetchError) as exc_info:
        parse_categories(_make_source({"categories": ["DROP TABLE"]}))
    assert exc_info.value.retryable is False


# ---- fetch ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_builds_paper_rawitems():
    fetcher = ArxivFetcher(max_retries=0)
    body = _atom(
        _entry("2601.00001v1", title="Attention  Is\n  Enough", published=_NOW - timedelta(days=1))
        + _entry("2601.00002v1", title="Second Paper", published=_NOW - timedelta(days=2))
    )
    with respx.mock() as mock:
        mock.get(url__startswith=API).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_make_source(), lookback_days=7)

    assert len(items) == 2
    first = items[0]
    assert first.url == "http://arxiv.org/abs/2601.00001v1"
    # Whitespace normalised in title + abstract.
    assert first.title == "Attention Is Enough"
    assert first.content_type == ContentType.paper
    assert "Ada Lovelace, Alan Turing" in first.content
    assert "cs.AI, cs.LG" in first.content
    assert first.metadata["feed_kind"] == "arxiv"
    assert first.metadata["arxiv_id"] == "2601.00001v1"
    assert first.metadata["pdf_url"] == "http://arxiv.org/pdf/2601.00001v1"
    assert first.author == "Ada Lovelace, Alan Turing"


@pytest.mark.asyncio
async def test_fetch_lookback_cutoff_stops_iteration():
    """结果按 submittedDate 倒序,首条过期即停(不含该条)。"""
    fetcher = ArxivFetcher(max_retries=0)
    body = _atom(
        _entry("2601.00001v1", title="Fresh", published=_NOW - timedelta(days=1))
        + _entry("2512.00001v1", title="Stale", published=_NOW - timedelta(days=40))
        + _entry("2511.00001v1", title="Ancient", published=_NOW - timedelta(days=400))
    )
    with respx.mock() as mock:
        mock.get(url__startswith=API).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_make_source(), lookback_days=7)
    assert [i.title for i in items] == ["Fresh"]


@pytest.mark.asyncio
async def test_fetch_total_failure_raises_fetch_error():
    fetcher = ArxivFetcher(max_retries=0)
    with respx.mock() as mock:
        mock.get(url__startswith=API).mock(return_value=Response(503, text="down"))
        with pytest.raises(FetchError):
            await fetcher.fetch(_make_source())


@pytest.mark.asyncio
async def test_fetch_accepts_lookback_days_kwarg():
    """Pipeline call-shape regression (the lookback_days lesson)."""
    fetcher = ArxivFetcher(max_retries=0)
    body = _atom(_entry("2601.00001v1", title="T", published=_NOW - timedelta(days=1)))
    with respx.mock() as mock:
        mock.get(url__startswith=API).mock(return_value=Response(200, content=body))
        items = await fetcher.fetch(_make_source(), lookback_days=30)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_fetch_bad_config_raises_before_any_request():
    fetcher = ArxivFetcher(max_retries=0)
    with respx.mock(assert_all_called=False):  # no HTTP call expected
        with pytest.raises(FetchError) as exc_info:
            await fetcher.fetch(_make_source({"categories": [123]}))
    assert exc_info.value.retryable is False
