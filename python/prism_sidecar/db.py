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
SCHEMA_VERSION = 2


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
"""


# v2 FTS5 DDL — kept outside SCHEMA_SQL so a v0.2a install (schema
# version 1) can be migrated forward to v2 without recreating the
# items / sources tables. The migration runs once, in order, in
# _run_migrations below.
#
# Tokenizer note: we use plain `unicode61` — do NOT pass
# `remove_diacritics 2`. The `remove_diacritics` flag incidentally
# treats non-Latin scripts differently in a way that breaks
# Chinese single-character prefix search (verified against
# sqlite3 3.45+: typing "开" no longer finds "开源" because
# unicode61+remove_diacritics merges adjacent CJK codepoints
# into multi-char tokens).
_V2_FTS5_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5("
    "title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh, "
    "content='items', content_rowid='rowid', "
    "tokenize='unicode61'"
    ")"
)


_db: aiosqlite.Connection | None = None


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
    A v0.2a install has `_meta.schema_version = '1'`. We:
      1. Apply the v1 baseline (idempotent CREATE TABLE IF NOT EXISTS).
      2. If schema_version < 2, create the FTS5 table + triggers
         and backfill the index from existing items.
      3. Bump schema_version to 2.
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

    if current_version < 2:
        # 2a. FTS5 virtual table.
        await db.execute(_V2_FTS5_DDL)

        # 2b. FTS5 sync triggers — one execute() per trigger, never
        #     inside executescript (see deadlock note above).
        for ddl in (
            "CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN "
            "INSERT INTO items_fts(rowid, title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh) "
            "VALUES (new.rowid, new.title_en, new.title_zh, new.summary_en, new.summary_zh, new.key_points_zh, new.tags_zh); "
            "END",
            "CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN "
            "INSERT INTO items_fts(items_fts, rowid, title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh) "
            "VALUES ('delete', old.rowid, old.title_en, old.title_zh, old.summary_en, old.summary_zh, old.key_points_zh, old.tags_zh); "
            "END",
            "CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN "
            "INSERT INTO items_fts(items_fts, rowid, title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh) "
            "VALUES ('delete', old.rowid, old.title_en, old.title_zh, old.summary_en, old.summary_zh, old.key_points_zh, old.tags_zh); "
            "INSERT INTO items_fts(rowid, title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh) "
            "VALUES (new.rowid, new.title_en, new.title_zh, new.summary_en, new.summary_zh, new.key_points_zh, new.tags_zh); "
            "END",
        ):
            await db.execute(ddl)

        # 2c. Backfill: copy any pre-existing items into the FTS
        #     index. Fresh installs have zero rows so this is a
        #     no-op. For a v0.2a upgrade, this materialises the
        #     search index without requiring a re-sync.
        await db.execute(
            "INSERT INTO items_fts(rowid, title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh) "
            "SELECT rowid, title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh "
            "FROM items"
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

    await _run_migrations(db)

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
]
