"""SQLite connection + schema migration.

A single aiosqlite connection is shared across the app via `get_db()`. We
keep the schema definition in one place so it's easy to audit and extend.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from prism_sidecar import config as _config

log = logging.getLogger(__name__)

# Use module-level access (not `from config import PRISM_DB_PATH`) so
# tests that monkeypatch `prism_sidecar.config.PRISM_DB_PATH` actually
# take effect when init_db() runs.
PRISM_DB_PATH = _config.PRISM_DB_PATH
PRISM_DATA_DIR = _config.PRISM_DATA_DIR

# Schema version. Bump on every migration; the `_meta` table records what
# the on-disk DB is at so we can run upgrade migrations in order.
#
# v1: initial schema (sources, items, sync_log, sync_jobs, _meta)
# v2: add FTS5 virtual table `items_fts` for full-text search across
#     title_en / title_zh / summary_en / summary_zh / key_points_zh /
#     tags_zh, with triggers that keep it in sync with the items table.
# v3: rebuild `items_fts` as a self-contained (non-external-content)
#     FTS5 table whose text is CJK-segmented in Python before insert
#     (see fts5.segment_cjk). unicode61 treats a CJK run as ONE token,
#     so the v2 index could never match "协作" inside "开源协作新工具";
#     per-char segmentation + phrase queries fixes Chinese substring
#     search. Index maintenance for INSERT/UPDATE moves into store.py
#     (SQL triggers can't segment); only the DELETE trigger remains.
# v4: add `webhooks` table (external-agent callbacks). Pure additive.
# v5 (v0.5): add `item_tags` table — user-applied tags, distinct from the
#     distiller's auto `items.tags_zh`. Pure additive (CREATE TABLE IF NOT
#     EXISTS in SCHEMA_SQL is idempotent on every init, like webhooks was),
#     so no dedicated migration step is needed — only the version bump.
SCHEMA_VERSION = 5


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_json TEXT,
    last_synced_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title_en TEXT NOT NULL,
    title_zh TEXT,
    summary_en TEXT,
    summary_zh TEXT,
    key_points_zh TEXT,
    tags_zh TEXT,
    author TEXT,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    distilled_at TEXT,
    status TEXT NOT NULL DEFAULT 'unread',
    content_type TEXT NOT NULL,
    duration_sec INTEGER,
    metadata_json TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    job_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    items_new INTEGER DEFAULT 0,
    items_distilled INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_log_job ON sync_log(job_id);
CREATE INDEX IF NOT EXISTS idx_sync_log_source ON sync_log(source_id);

CREATE TABLE IF NOT EXISTS sync_jobs (
    job_id TEXT PRIMARY KEY,
    source_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    items_new INTEGER DEFAULT 0,
    items_distilled INTEGER DEFAULT 0,
    sources_total INTEGER DEFAULT 0,
    sources_done INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_jobs_status ON sync_jobs(status);

CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- v4 (v0.3): webhook registrations. External agents register a callback URL
-- (via the MCP prism_register_webhook tool); the sidecar POSTs matching new
-- items to it after a sync. Pure additive table — no rebuild migration.
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    source_id TEXT,
    tag TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    fail_streak INTEGER NOT NULL DEFAULT 0,
    last_status TEXT,
    last_delivered_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_webhooks_enabled ON webhooks(enabled);

-- v5 (v0.5): user-applied tags. Distinct from the distiller's auto
-- `items.tags_zh` (which stays as-is and remains FTS-searchable). One row
-- per (item, tag); ON DELETE CASCADE drops an item's tags when it's removed.
CREATE TABLE IF NOT EXISTS item_tags (
    item_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (item_id, tag),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag);
"""


# v3 FTS5 DDL — kept outside SCHEMA_SQL so older installs can be
# migrated forward without recreating the items / sources tables.
# The migration runs once, in order, in _run_migrations below.
#
# v3 design notes:
#   * NOT an external-content table (v2 used content='items'). The
#     indexed text is the CJK-segmented form (fts5.segment_cjk), which
#     differs from what the items table stores, so the index must own
#     its copy of the text.
#   * Plain `unicode61` tokenizer. Segmentation happens in Python;
#     the tokenizer just splits on the spaces we inserted.
#   * INSERT/UPDATE maintenance lives in store.py (_fts_upsert). Only
#     row deletion is handled by a trigger (a rowid-based DELETE needs
#     no segmentation, and it also covers ON DELETE CASCADE from
#     sources → items).
_V3_FTS5_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5("
    "title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh, "
    "tokenize='unicode61'"
    ")"
)

_V3_DELETE_TRIGGER_DDL = (
    "CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN "
    "DELETE FROM items_fts WHERE rowid = old.rowid; "
    "END"
)


_db: aiosqlite.Connection | None = None

# v0.5: whether the sqlite-vec extension loaded and the `items_vec` vector
# table is usable. Best-effort — if the extension can't load (e.g. a
# sqlite3 build with extension loading disabled, or the frozen binary
# without the bundled .dylib), semantic search is simply off and the app
# runs on FTS5. Read via `vec_available()`.
_vec_available: bool = False


def vec_available() -> bool:
    """True when the sqlite-vec `items_vec` table is loaded and usable."""
    return _vec_available


async def _init_vec(db: aiosqlite.Connection) -> None:
    """Load sqlite-vec and create the `items_vec` vector table (best-effort).

    The table is a vec0 virtual table keyed by item_id, one row per
    embedded item. It is NOT part of SCHEMA_SQL because it depends on the
    extension being loaded first, and creating it is idempotent
    (IF NOT EXISTS), so it's rebuildable without a schema-version bump.
    """
    global _vec_available
    try:
        import sqlite_vec  # noqa: PLC0415 — optional, only needed here
        from prism_sidecar.embeddings import EMBED_DIM

        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        await db.enable_load_extension(False)
        await db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0("
            f"item_id TEXT PRIMARY KEY, embedding FLOAT[{EMBED_DIM}])"
        )
        await db.commit()
        _vec_available = True
        log.info("[prism-sidecar] sqlite-vec loaded; semantic search enabled")
    except Exception as e:  # pragma: no cover - env-dependent
        _vec_available = False
        log.warning(
            "[prism-sidecar] sqlite-vec unavailable (%s); semantic search off, "
            "falling back to FTS5",
            e,
        )


def _ensure_data_dir() -> None:
    """Create the data directory if it doesn't exist."""
    _config.PRISM_DATA_DIR.mkdir(parents=True, exist_ok=True)


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending schema migrations.

    v0.2a ships v1; v2 adds the FTS5 virtual table for full-text
    search. The FTS5 trigger DDL must be issued one statement at a
    time rather than inside `executescript` — aiosqlite wraps
    executescript in an implicit transaction, and trigger DDL
    re-enters the schema lock the same worker thread is holding,
    which deadlocks the connection (test fixtures hang
    indefinitely). Plain CREATE TABLE / INDEX is fine inside
    executescript.

    Upgrade path
    ------------
    A v0.2a install has `_meta.schema_version = '1'`; v0.2b has '2'.
    We:
      1. Apply the v1 baseline (idempotent CREATE TABLE IF NOT EXISTS).
      2. If schema_version < 3, (re)build the v3 FTS5 table: drop the
         v2 external-content table + its triggers if present, create
         the self-contained table + delete trigger, and backfill the
         index from existing items with CJK segmentation applied.
      3. Bump schema_version to 3.
    """
    # Step 1: baseline (v1) DDL — all idempotent.
    await db.executescript(SCHEMA_SQL)

    # Step 2: read current version (default to 0 for fresh installs
    # that just got the _meta table above).
    cur = await db.execute(
        "SELECT value FROM _meta WHERE key = 'schema_version'"
    )
    row = await cur.fetchone()
    current_version = int(row[0]) if row else 0

    if current_version < 3:
        # 3a. Drop the v2 layout if it exists (external-content FTS
        #     table + the three sync triggers). One execute() per
        #     statement — trigger DDL inside executescript deadlocks
        #     (see note above).
        for ddl in (
            "DROP TRIGGER IF EXISTS items_ai",
            "DROP TRIGGER IF EXISTS items_ad",
            "DROP TRIGGER IF EXISTS items_au",
            "DROP TABLE IF EXISTS items_fts",
        ):
            await db.execute(ddl)

        # 3b. v3 FTS5 table + delete trigger.
        await db.execute(_V3_FTS5_DDL)
        await db.execute(_V3_DELETE_TRIGGER_DDL)

        # 3c. Backfill with CJK segmentation. Fresh installs have zero
        #     rows so this is a no-op; upgrades get their search index
        #     rebuilt without a re-sync. Imported lazily to avoid a
        #     module-load cycle (fts5.py has no db import, but keep
        #     db.py import-cheap for tests).
        from prism_sidecar.fts5 import segment_cjk

        cur = await db.execute(
            "SELECT rowid, title_en, title_zh, summary_en, summary_zh, "
            "key_points_zh, tags_zh FROM items"
        )
        rows = await cur.fetchall()
        for r in rows:
            await db.execute(
                "INSERT INTO items_fts(rowid, title_en, title_zh, summary_en, "
                "summary_zh, key_points_zh, tags_zh) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r[0], *(segment_cjk(v) for v in r[1:])),
            )

    # Step 3: bump schema_version to the current target.
    await db.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    await db.commit()


async def init_db(db_path: Path | None = None) -> aiosqlite.Connection:
    """Initialize the database; returns the connection.

    Idempotent — safe to call multiple times.
    """
    global _db
    if _db is not None:
        return _db

    _ensure_data_dir()
    target = db_path or _config.PRISM_DB_PATH

    db = await aiosqlite.connect(str(target))
    # Foreign keys are off by default in SQLite; turn them on so ON DELETE
    # CASCADE works for items.
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA journal_mode = WAL")
    # WAL allows one writer at a time. Without a busy timeout a concurrent
    # write (e.g. the read/write MCP process registering a source while the
    # sidecar is mid-sync) raises SQLITE_BUSY immediately instead of waiting.
    # Wait up to 5s for the lock before giving up.
    await db.execute("PRAGMA busy_timeout = 5000")

    await _run_migrations(db)
    # v0.5: optional vector index for semantic search. Best-effort — never
    # blocks startup; sets vec_available() accordingly.
    await _init_vec(db)

    _db = db
    log.info("[prism-sidecar] db initialized at %s", target)
    return db


async def close_db() -> None:
    """Close the shared connection (called from FastAPI lifespan)."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def get_db() -> aiosqlite.Connection:
    """Return the shared connection. Caller must `await init_db()` first."""
    if _db is None:
        raise RuntimeError("DB not initialized — call init_db() during startup")
    return _db


@asynccontextmanager
async def db_session() -> AsyncIterator[aiosqlite.Connection]:
    """Context manager that yields a fresh connection (for tests).

    Use this in test code that wants to run against a tmp file. In
    production, prefer `get_db()`.
    """
    _ensure_data_dir()
    async with aiosqlite.connect(str(_config.PRISM_DB_PATH)) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(SCHEMA_SQL)
        yield db


__all__ = [
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "init_db",
    "close_db",
    "get_db",
    "db_session",
    "vec_available",
]
