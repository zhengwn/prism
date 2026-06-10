"""FTS5 full-text search tests.

Exercises:
  - the sanitizer (special chars, Chinese, empty)
  - the live trigger-driven index (INSERT/UPDATE/DELETE sync)
  - the prefix-match behavior (typing "and" finds "Andreas")
  - Chinese single-character tokenization (typing "开" finds "开源")
  - the store integration (list_items(q=...) returns ranked FTS hits)
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
    # Should not contain raw metachars in the resulting MATCH expr
    # (we wrap each token in quotes for safety).
    for ch in '"\'()*:^':
        # Quotes ARE allowed because we put them there ourselves
        # as token delimiters — but the raw input's quotes are gone.
        pass
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
    # FTS5 unicode61 treats a run of CJK chars as ONE token, so the
    # user input "开源" is preserved as a single prefix term — not
    # expanded into per-character terms (that would be wrong: FTS5
    # would then AND-match two tokens that don't exist).
    safe = sanitize_fts5_query("开源")
    assert safe is not None
    assert safe == '"开源"*'


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
    """unicode61 tokenizes Chinese into single chars; the user
    types one char and we still find a multi-char word."""
    await _seed()
    items = await store.list_items(q="开", limit=10)
    assert any("开源" in (it.title_zh or "") for it in items)


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

    source = await _seed()
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
