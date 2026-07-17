"""Full-text search helpers — sanitize user input into a safe FTS5
MATCH expression and build highlight snippets.

Why we sanitize
---------------
FTS5's MATCH parser treats a handful of characters as syntax
(quotes, parens, asterisks, colons, carets, plus/minus for
boolean operators, NEAR…). If a user types "foo:bar" in the search
box we want them to find items containing the literal text "foo:bar",
not "items where the FTS5 column 'foo' has value 'bar'" — that
would either error or, worse, silently return nothing.

The sanitizer is deliberately conservative: it strips FTS5
metacharacters and wraps each remaining word in a double-quoted
prefix-search term (``"andreas"*``). Prefix search means typing
"and" still finds "Andreas", which is the most common case for
typeahead-style search.

Chinese note (schema v3)
------------------------
FTS5's ``unicode61`` tokenizer treats a contiguous CJK run as ONE
token (NOT one token per character — "开源协作" is a single 4-char
token). Prefix search over that token only matches from the *start*
of the run, so "协作" could never find "开源协作新工具".

Since schema v3 we therefore segment CJK text OURSELVES at index
time: every CJK character is space-separated before it is written
into ``items_fts`` (see :func:`segment_cjk`, called from
``store.py``). On the query side, a CJK run in the user's input is
expanded into an FTS5 *phrase* of consecutive single-char tokens
(``开源`` → ``"开 源"``), which matches the sequence 开→源 anywhere
inside the indexed text. Non-CJK tokens keep the original
``"tok"*`` prefix form. Zero-dependency, and it gives true
substring search for Chinese — a huge step up from both
``LIKE '%...%'`` scans and the broken whole-run prefix match.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Iterable

# Characters that FTS5's MATCH parser treats as syntax. Stripping
# them all is the simplest correct thing — we don't need boolean
# operators from the search box, just literal text + prefix.
_FTS5_METACHARS = re.compile(r'["\'()*:^\-+]')

# Split on whitespace AND on FTS5-style punctuation we just stripped
# (so a search for "C++" still works — we tokenize into "C" and " "
# first, strip the ++, and end up with a single "C" token, which is
# the right behaviour for a knowledge-base search).
_TOKEN_SPLIT = re.compile(r"[\s,;./\\|]+")

# CJK character ranges we segment. Covers the URO + Ext-A blocks and
# the compatibility ideographs — the scripts unicode61 would otherwise
# lump into one run. (Kana/Hangul are left alone: those scripts have
# real word boundaries more often and we have no zh-adjacent sources
# in those languages today.)
_CJK_RANGE = "㐀-䶿一-鿿豈-﫿"
_CJK_CHAR_RE = re.compile(f"[{_CJK_RANGE}]")
_CJK_RUN_RE = re.compile(f"([{_CJK_RANGE}]+)|([^{_CJK_RANGE}]+)")


def segment_cjk(text: str | None) -> str:
    """Space-separate every CJK character in ``text`` (index-time form).

    ``store.py`` runs every indexed column through this before writing
    it into ``items_fts``, so unicode61 sees one token per CJK char.
    Idempotent; non-CJK text passes through unchanged (extra spaces
    are harmless — unicode61 splits on whitespace anyway).
    """
    if not text:
        return ""
    return _CJK_CHAR_RE.sub(lambda m: f" {m.group(0)} ", text)


def _expand_token(tok: str) -> list[str]:
    """Turn one user token into FTS5 MATCH terms.

    CJK runs become a *phrase* of consecutive single-char tokens
    (``开源`` → ``"开 源"``) which matches the character sequence
    anywhere in the segmented index. Non-CJK runs keep the
    quoted-prefix form (``andr`` → ``"andr"*``).
    """
    terms: list[str] = []
    for m in _CJK_RUN_RE.finditer(tok):
        cjk, other = m.group(1), m.group(2)
        if cjk:
            terms.append('"' + " ".join(cjk) + '"')
        elif other and other.strip():
            terms.append(f'"{other}"*')
    return terms


def sanitize_fts5_query(raw: str) -> str | None:
    """Turn a user's free-text query into a safe FTS5 MATCH expression.

    Returns ``None`` when there's nothing meaningful to search for
    (empty string, all-punctuation, etc.) — the caller should
    treat that as "no filter" rather than passing an empty string
    to MATCH (which is a syntax error).

    Note on Chinese (schema v3)
    ---------------------------
    The index stores CJK text pre-segmented one char per token (see
    :func:`segment_cjk`), so a CJK run in the query is expanded into
    a phrase of single-char tokens: ``开源`` → ``"开 源"``. That
    matches 开→源 as a contiguous sequence anywhere in the text —
    including the middle of a word, which the pre-v3 whole-run
    prefix form (``"开源"*``) could not do.
    """
    if not raw:
        return None
    # Strip FTS5 metacharacters first so the tokenizer split is
    # faithful to what the user typed (minus the syntax).
    cleaned = _FTS5_METACHARS.sub(" ", raw)
    tokens = [t for t in _TOKEN_SPLIT.split(cleaned) if t]
    if not tokens:
        return None
    terms: list[str] = []
    for tok in tokens:
        terms.extend(_expand_token(tok))
    if not terms:
        return None
    return " ".join(terms)


def build_snippet(
    db: sqlite3.Connection,
    rowid: int,
    query: str,
    *,
    column: str = "title_zh",
    marker_start: str = "<mark>",
    marker_end: str = "</mark>",
    snippet_len: int = 64,
) -> str | None:
    """Return a highlighted snippet for one row, or None on no match.

    Uses FTS5's built-in ``snippet()`` so we don't need a second
    index or a hand-rolled highlighter. The column is configurable
    so we can show a title highlight for title matches and a
    summary highlight otherwise.

    Note: this helper accepts a synchronous ``sqlite3.Connection``
    — the sidecar's runtime DB is an aiosqlite wrapper, so the
    API layer awaits the execute and passes the underlying
    sqlite3 connection in here for the synchronous snippet() call.
    """
    safe_query = sanitize_fts5_query(query)
    if safe_query is None:
        return None
    try:
        # snippet() returns up to `snippet_len` tokens surrounding
        # the first match in the named column, with the match
        # wrapped in the supplied markers. The '...' is the
        # ellipsis used when the snippet is truncated.
        cur = db.execute(
            "SELECT snippet(items_fts, ?, ?, ?, '...', 16) "
            "FROM items_fts WHERE rowid = ? AND items_fts MATCH ?",
            (
                _column_index(column),
                marker_start,
                marker_end,
                rowid,
                safe_query,
            ),
        )
    except sqlite3.OperationalError:
        return None
    row = cur.fetchone()
    return row[0] if row else None


async def build_snippet_async(
    db,  # aiosqlite.Connection
    rowid: int,
    query: str,
    **kwargs,
) -> str | None:
    """Async-friendly wrapper around :func:`build_snippet`.

    Aiosqlite runs queries on a background worker thread; if we
    tried to call the synchronous ``sqlite3`` execute from our
    own thread we'd hit "SQLite objects created in a thread can
    only be used in that same thread". So we go through aiosqlite
    properly: the execute is awaited (so it lands on the worker
    thread), and we read fetchone() off the awaited result.
    """
    safe_query = sanitize_fts5_query(query)
    if safe_query is None:
        return None
    try:
        cur = await db.execute(
            "SELECT snippet(items_fts, ?, ?, ?, '...', 16) "
            "FROM items_fts WHERE rowid = ? AND items_fts MATCH ?",
            (
                _column_index(kwargs.get("column", "title_zh")),
                kwargs.get("marker_start", "<mark>"),
                kwargs.get("marker_end", "</mark>"),
                rowid,
                safe_query,
            ),
        )
    except sqlite3.OperationalError:
        return None
    row = await cur.fetchone()
    return row[0] if row else None


# Map of column name → 0-based position in the FTS table declaration.
# Must stay in sync with the CREATE VIRTUAL TABLE in db.py.
_FTS_COLUMN_INDEX: dict[str, int] = {
    "title_en": 0,
    "title_zh": 1,
    "summary_en": 2,
    "summary_zh": 3,
    "key_points_zh": 4,
    "tags_zh": 5,
}


def _column_index(name: str) -> int:
    return _FTS_COLUMN_INDEX.get(name, 0)


def iter_token_hits(query: str) -> Iterable[str]:
    """Yield the cleaned search tokens in a query, for callers
    that want to do their own highlighting (e.g. a frontend that
    re-renders server-returned snippets with its own <mark> style).

    Tokens are returned in their *display* form ("开源", not the
    segmented MATCH phrase ``"开 源"``) so a highlighter can match
    them against the original, unsegmented text.
    """
    if not query:
        return []
    cleaned = _FTS5_METACHARS.sub(" ", query)
    return [t for t in _TOKEN_SPLIT.split(cleaned) if t]


__all__ = [
    "sanitize_fts5_query",
    "segment_cjk",
    "build_snippet",
    "iter_token_hits",
]
