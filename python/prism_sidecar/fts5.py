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

Chinese note
------------
We use the FTS5 ``unicode61`` tokenizer (configured in db.py)
which by default splits on non-letter characters. That means
"开源协作" becomes the tokens "开" "源" "协" "作" — single
characters. Combined with prefix search, typing "开" finds
"开源" and typing "开源" finds anything containing the
contiguous sequence 开→源. Not as good as jieba word
segmentation but zero-dependency and a huge step up from
``LIKE '%...%'`` scans.
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


def sanitize_fts5_query(raw: str) -> str | None:
    """Turn a user's free-text query into a safe FTS5 MATCH expression.

    Returns ``None`` when there's nothing meaningful to search for
    (empty string, all-punctuation, etc.) — the caller should
    treat that as "no filter" rather than passing an empty string
    to MATCH (which is a syntax error).

    Note on Chinese
    ----------------
    FTS5's ``unicode61`` tokenizer (the one we use) treats a run
    of CJK characters as ONE token, not one per character. So the
    column stream for the title "Hugging Face 开源协作新工具" is
    approximately ``hugging`` ``face`` ``开源协作新工具`` — the
    Chinese portion is a single 7-char token.

    Consequence: a user typing "开源" needs the query
    ``"开源"*`` (a single prefix term), NOT ``"开"* "源"*``
    (which would AND-match two tokens that don't exist in the
    column). The sanitizer below leaves Chinese substrings
    intact and only wraps them in the FTS5 prefix form.
    """
    if not raw:
        return None
    # Strip FTS5 metacharacters first so the tokenizer split is
    # faithful to what the user typed (minus the syntax).
    cleaned = _FTS5_METACHARS.sub(" ", raw)
    tokens = [t for t in _TOKEN_SPLIT.split(cleaned) if t]
    if not tokens:
        return None
    # Wrap each token in a double-quoted FTS5 string + a trailing
    # `*` for prefix match. Quoting defends against the (very
    # unlikely after our strip) chance of a stray metachar and
    # also tells FTS5 to treat the token as a literal, not a
    # column-name expression.
    return " ".join(f'"{tok}"*' for tok in tokens)


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
    """Yield the sanitized search tokens in a query, for callers
    that want to do their own highlighting (e.g. a frontend that
    re-renders server-returned snippets with its own <mark> style).
    """
    safe = sanitize_fts5_query(query)
    if safe is None:
        return []
    # Strip the surrounding "..." and trailing * we added so callers
    # get clean tokens back.
    return [tok.strip('"*') for tok in safe.split()]


__all__ = [
    "sanitize_fts5_query",
    "build_snippet",
    "iter_token_hits",
]
