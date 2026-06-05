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
SCHEMA_VERSION = 1


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


_db: aiosqlite.Connection | None = None


def _ensure_data_dir() -> None:
    """Create the data directory if it doesn't exist."""
    _config.PRISM_DATA_DIR.mkdir(parents=True, exist_ok=True)


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending schema migrations.

    v0.2a only ships v1; future versions should add idempotent ALTER TABLE
    statements gated on the value of `_meta.schema_version`.
    """
    await db.executescript(SCHEMA_SQL)
    await db.execute(
        "INSERT OR IGNORE INTO _meta(key, value) VALUES('schema_version', ?)",
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
