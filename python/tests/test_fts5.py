"""FTS5 full-text search tests (schema v3).

Exercises:
  - the sanitizer (special chars, Chinese, empty)
  - the store-maintained index (INSERT/UPDATE via _fts_upsert,
    DELETE via trigger)
  - the prefix-match behavior (typing "and" finds "Andreas")
  - Chinese substring search via per-char segmentation (typing
    "协作" finds "开源协作新工具" — mid-run matches, not just
    run prefixes)
  - the store integration (list_items(q=...) returns ranked FTS
    hits, respecting source/status filters)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prism_sidecar import store
from prism_sidecar.db import init_db
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.fts5 import (
    build_snippet_async,
    iter_token_hits,
    sanitize_fts5_query,
)

# ---- pure-function sanitizer --------------------------------------------


def test_sanitize_fts5_query_empty_returns_none():
    assert sanitize_fts5_query("") is None
    assert sanitize_fts5_query("   ") is None
    # All-FTS5-metachar input has nothing left after stripping
    assert sanitize_fts5_query("()*:^-") is None


def test_sanitize_fts5_query_strips_metachars():
    # FTS5 syntax chars are dropped; the literal "andreas" survives
    # and gets prefix-wrapped.
    safe = sanitize_fts5_query('"andreas" +kling -foo:bar')
    assert safe is not None
    # Should not contain raw metachars in the resulting MATCH expr.
    # `"` and `*` are excluded from the check: _expand_token emits those
    # itself as token delimiters / prefix markers — the raw input's are
    # gone either way.
    for ch in "'():^":
        assert ch not in safe
    # The literal words survive (we lost "foo" because "foo:bar" was
    # treated as `foo bar` after stripping ":")
    assert "andreas" in safe
    assert "kling" in safe


def test_sanitize_fts5_query_prefix_match():
    # Trailing * is what makes it a prefix search. The user types
    # "andr" and we want to find "Andreas".
    safe = sanitize_fts5_query("andr")
    assert safe is not None
    assert safe.endswith("*")


def test_sanitize_fts5_query_chinese():
    # Schema v3: the index stores CJK text one char per token
    # (fts5.segment_cjk), so a CJK run in the query becomes a
    # phrase of consecutive single-char tokens. This is what makes
    # mid-word matches ("协作" inside "开源协作新工具") possible —
    # the pre-v3 form '"开源"*' could only match run prefixes.
    safe = sanitize_fts5_query("开源")
    assert safe is not None
    assert safe == '"开 源"'


def test_sanitize_fts5_query_mixed_ascii_cjk():
    # Mixed tokens split into an ASCII prefix term + a CJK phrase.
    safe = sanitize_fts5_query("GPT5开源")
    assert safe == '"GPT5"* "开 源"'


def test_iter_token_hits_returns_clean_tokens():
    # Frontend uses this to highlight matches in the rendered text
    # without re-implementing the sanitizer. For ASCII input the
    # tokenizer returns one entry per word; Chinese substrings stay
    # as single entries (mirroring the sanitizer).
    tokens = list(iter_token_hits("andreas kling"))
    assert tokens == ["andreas", "kling"]
    tokens = list(iter_token_hits("开源"))
    assert tokens == ["开源"]


# ---- live FTS5 index against the real DB --------------------------------


@pytest.fixture
async def initialized():
    await init_db()
    yield


async def _seed(source_name: str = "S"):
    """Seed three items: two English-only, one with Chinese text in
    both title and summary. We use title_en for English (mandatory
    NOT NULL) and also stuff Chinese into summary_en + title_en so
    the FTS index has Chinese tokens to match against even before
    distillation runs.
    """
    from prism_sidecar.distillers.base import DistilledItem

    source = await store.create_source(source_name, "rss", "https://x")
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    seeds = [
        ("https://a/post-1", "Andreas Kling refuses public PRs", "AI era changes how we read maintainer intent", None),
        ("https://a/post-2", "OpenAI ships GPT-5", "Native multimodal with tool use", None),
        ("https://a/post-3", "Hugging Face 开源协作新工具", "中文社区的协作模式在变",
         DistilledItem(
             title_zh="Hugging Face 开源协作新工具",
             summary_zh="中文社区的协作模式在变",
             key_points_zh=[],
             tags_zh=[],
         )),
    ]
    item_ids: list[str] = []
    for i, (url, title, summary, distilled) in enumerate(seeds):
        raw = RawItem(
            url=url, title=title, content=summary,
            published_at=base + timedelta(hours=i),
        )
        item_id = await store.insert_item_from_raw(source, raw)
        item_ids.append(item_id)
        if distilled is not None:
            await store.update_item_distilled(item_id, distilled)
    return source


@pytest.mark.asyncio
async def test_fts5_finds_by_english_word(initialized):
    await _seed()
    items = await store.list_items(q="Andreas", limit=10)
    assert len(items) == 1
    assert "Andreas" in items[0].title_en


@pytest.mark.asyncio
async def test_fts5_prefix_match(initialized):
    """Typing 'andr' should find 'Andreas' (prefix search)."""
    await _seed()
    items = await store.list_items(q="andr", limit=10)
    assert any("Andreas" in it.title_en for it in items)


@pytest.mark.asyncio
async def test_fts5_finds_chinese(initialized):
    await _seed()
    items = await store.list_items(q="开源", limit=10)
    assert any("开源" in (it.title_zh or "") for it in items)


@pytest.mark.asyncio
async def test_fts5_chinese_single_char(initialized):
    """The v3 index stores one token per CJK char; a single-char
    query matches it anywhere in the text."""
    await _seed()
    items = await store.list_items(q="开", limit=10)
    assert any("开源" in (it.title_zh or "") for it in items)


@pytest.mark.asyncio
async def test_fts5_chinese_midword_substring(initialized):
    """Regression (v3): '协作' sits in the MIDDLE of the CJK run
    "开源协作新工具". The v2 whole-run prefix index could never
    match this; per-char segmentation + phrase queries must."""
    await _seed()
    items = await store.list_items(q="协作", limit=10)
    matched = [
        it for it in items
        if "协作" in (it.title_zh or "") or "协作" in (it.summary_en or "")
    ]
    assert matched, "mid-word Chinese substring should match"
    # And "工具" (tail of the run) too.
    items = await store.list_items(q="工具", limit=10)
    assert any("工具" in (it.title_zh or "") for it in items)


@pytest.mark.asyncio
async def test_fts5_search_respects_source_and_status_filters(initialized):
    """Regression: the FTS path used to silently DROP the source_id /
    status filters, so searching with a sidebar filter active
    returned unfiltered results."""
    from prism_sidecar.models import ItemStatus

    source = await _seed()
    other = await store.create_source("Other", "rss", "https://y")
    raw = RawItem(
        url="https://b/post-1",
        title="Andreas writes another post",
        content="unrelated",
        published_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    other_item_id = await store.insert_item_from_raw(other, raw)

    # Unfiltered search sees both sources' "Andreas" items.
    items = await store.list_items(q="Andreas", limit=10)
    assert len(items) == 2

    # source_id filter narrows to one.
    items = await store.list_items(q="Andreas", source_id=other.id, limit=10)
    assert [it.id for it in items] == [other_item_id]

    # status filter applies too.
    await store.update_item_status(other_item_id, ItemStatus.starred)
    items = await store.list_items(q="Andreas", status="starred", limit=10)
    assert [it.id for it in items] == [other_item_id]
    items = await store.list_items(q="Andreas", source_id=source.id, status="starred", limit=10)
    assert items == []


@pytest.mark.asyncio
async def test_fts5_no_match_returns_empty(initialized):
    await _seed()
    items = await store.list_items(q="xyzzyzzz", limit=10)
    assert items == []


@pytest.mark.asyncio
async def test_fts5_special_chars_dont_crash(initialized):
    """The sanitizer must neutralise FTS5 syntax so the route
    returns 0 results rather than a 500 from a syntax error."""
    await _seed()
    # All of these used to be either 500s or `LIKE '%...%'`
    # scans; now they all just gracefully return whatever matches.
    for q in ['"foo"', "(bar)", "foo:bar", "a*", "+must -not", "C++"]:
        items = await store.list_items(q=q, limit=10)
        assert isinstance(items, list)


@pytest.mark.asyncio
async def test_fts5_index_tracks_delete(initialized):
    source = await _seed()
    items = await store.list_items(q="GPT-5", limit=10)
    assert len(items) == 1
    # Delete via the store's source delete (CASCADE drops items,
    # trigger should drop the FTS row).
    await store.delete_source(source.id)
    items = await store.list_items(q="GPT-5", limit=10)
    assert items == []


@pytest.mark.asyncio
async def test_fts5_index_tracks_update(initialized):
    """Update the distilled_at + summary_zh fields; the FTS row
    must reflect the new content (not the old)."""
    from prism_sidecar.distillers.base import DistilledItem

    await _seed()
    items = await store.list_items(q="GPT-5", limit=10)
    item_id = items[0].id
    # Original summary_en is "Native multimodal with tool use" —
    # not in the FTS index yet because we only indexed title_en etc.
    # Update with summary_zh that has a unique token.
    await store.update_item_distilled(
        item_id,
        DistilledItem(
            title_zh="OpenAI 发布 GPT-5",
            summary_zh="原生多模态支持工具调用，性能超越上一代",
            key_points_zh=[],
            tags_zh=[],
        ),
    )
    # New token "原生" should now be searchable.
    items = await store.list_items(q="原生", limit=10)
    assert any(it.id == item_id for it in items)


@pytest.mark.asyncio
async def test_fts5_snippet_builds_highlight(initialized):
    """The snippet helper wraps matches in <mark> tags so the
    frontend can render them with a custom style."""
    await _seed()
    # Look up the rowid for the Andreas item.
    items = await store.list_items(q="Andreas", limit=10)
    assert items, "expected at least one match"
    # `build_snippet` needs a sqlite3 connection — use get_db
    # (which is the same shared connection the app uses).
    from prism_sidecar.db import get_db

    # rowid = items[0].id, but we need numeric rowid. Fetch it.
    db = get_db()
    cur = await db.execute("SELECT rowid FROM items WHERE id = ?", (items[0].id,))
    row = await cur.fetchone()
    rowid = row[0]
    snippet = await build_snippet_async(db, rowid, "Andreas", column="title_en")
    assert snippet is not None
    assert "<mark>" in snippet
    assert "</mark>" in snippet
    assert "Andreas" in snippet
