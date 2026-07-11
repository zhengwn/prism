"""SQLite-backed data layer (replaces the v0.1 in-memory store).

All functions are async because aiosqlite returns awaitables. The store
shares the single aiosqlite connection opened by `db.init_db()`.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from prism_sidecar.db import get_db
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import (
    ContentType,
    ItemStatus,
    KnowledgeItem,
    Source,
    SourceKind,
    SyncJobStatus,
    SyncLogEntry,
    SyncResult,
    Webhook,
)

log = logging.getLogger(__name__)


_started_at: datetime = datetime.now(timezone.utc)


# ----- Helpers ------------------------------------------------------------

def _row_to_source(row: tuple) -> Source:
    """Map a `sources` row tuple to a Source model.

    Column order matches `SELECT` statements in this module.
    """
    (
        sid,
        name,
        kind,
        url,
        enabled,
        config_json,
        last_synced_at,
        last_error,
        created_at,
        item_count,
    ) = row
    return Source(
        id=sid,
        name=name,
        kind=SourceKind(kind),
        url=url,
        enabled=bool(enabled),
        config_json=json.loads(config_json) if config_json else {},
        last_synced_at=_parse_iso(last_synced_at),
        last_error=last_error,
        created_at=_parse_iso(created_at),
        item_count=int(item_count or 0),
    )


def _row_to_item(row: tuple, source_name: str | None = None) -> KnowledgeItem:
    (
        iid,
        sid,
        url,
        title_en,
        title_zh,
        summary_en,
        summary_zh,
        key_points_zh_json,
        tags_zh_json,
        author,
        published_at,
        fetched_at,
        distilled_at,
        status,
        content_type,
        duration_sec,
        metadata_json,
    ) = row
    return KnowledgeItem(
        id=iid,
        source_id=sid,
        source_name=source_name or sid,
        url=url,
        title_en=title_en,
        title_zh=title_zh,
        summary_en=summary_en,
        summary_zh=summary_zh,
        key_points_zh=json.loads(key_points_zh_json) if key_points_zh_json else [],
        tags_zh=json.loads(tags_zh_json) if tags_zh_json else [],
        author=author,
        published_at=_parse_iso(published_at) or datetime.now(timezone.utc),
        fetched_at=_parse_iso(fetched_at) or datetime.now(timezone.utc),
        distilled_at=_parse_iso(distilled_at),
        status=ItemStatus(status),
        content_type=ContentType(content_type),
        duration_sec=int(duration_sec) if duration_sec is not None else None,
        metadata_json=json.loads(metadata_json) if metadata_json else {},
    )


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            # Python 3.11+ supports the "Z" suffix in fromisoformat.
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _fts_upsert(db, item_id: str) -> None:
    """(Re)write the FTS5 index row for one item.

    Schema v3: `items_fts` is self-contained and stores the
    CJK-segmented form of the text (see fts5.segment_cjk), so index
    maintenance for INSERT/UPDATE happens here in Python rather than
    in SQL triggers (triggers can't segment). Deletion is still
    trigger-driven (rowid-based, no segmentation needed).
    """
    from prism_sidecar.fts5 import segment_cjk

    cur = await db.execute(
        "SELECT rowid, title_en, title_zh, summary_en, summary_zh, "
        "key_points_zh, tags_zh FROM items WHERE id = ?",
        (item_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return
    rowid = row[0]
    await db.execute("DELETE FROM items_fts WHERE rowid = ?", (rowid,))
    await db.execute(
        "INSERT INTO items_fts(rowid, title_en, title_zh, summary_en, "
        "summary_zh, key_points_zh, tags_zh) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rowid, *(segment_cjk(v) for v in row[1:])),
    )


# ----- Health -------------------------------------------------------------

async def health_snapshot() -> dict:
    from prism_sidecar import __version__
    import prism_sidecar.config as _cfg
    from prism_sidecar import settings as _settings
    PRISM_DB_PATH = _cfg.PRISM_DB_PATH

    db = get_db()
    cur = await db.execute("SELECT COUNT(*) FROM sources")
    sources_count = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM items")
    items_count = (await cur.fetchone())[0]
    # v0.2a+: check the *active* provider (DeepSeek or MiniMax) rather
    # than the v0.1 DeepSeek-only helper.
    active = _settings.load_active_provider()
    return {
        "ok": True,
        "version": __version__,
        "sources_count": int(sources_count),
        "items_count": int(items_count),
        "distiller_configured": _settings.is_provider_configured(active["provider"]),
        "db_path": str(PRISM_DB_PATH),
        "uptime_sec": int((datetime.now(timezone.utc) - _started_at).total_seconds()),
    }


# ----- Sources ------------------------------------------------------------

async def list_sources() -> list[Source]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT s.id, s.name, s.kind, s.url, s.enabled, s.config_json,
               s.last_synced_at, s.last_error, s.created_at,
               (SELECT COUNT(*) FROM items WHERE source_id = s.id) AS item_count
        FROM sources s
        ORDER BY s.created_at ASC
        """
    )
    rows = await cur.fetchall()
    return [_row_to_source(r) for r in rows]


async def get_source(source_id: str) -> Optional[Source]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT s.id, s.name, s.kind, s.url, s.enabled, s.config_json,
               s.last_synced_at, s.last_error, s.created_at,
               (SELECT COUNT(*) FROM items WHERE source_id = s.id) AS item_count
        FROM sources s
        WHERE s.id = ?
        """,
        (source_id,),
    )
    row = await cur.fetchone()
    return _row_to_source(row) if row else None


async def create_source(
    name: str,
    kind: str,
    url: str,
    enabled: bool = True,
    config_json: Optional[dict[str, Any]] = None,
) -> Source:
    db = get_db()
    new = Source(
        id=_new_id("src"),
        name=name,
        kind=SourceKind(kind),
        url=url,
        enabled=enabled,
        config_json=config_json or {},
        item_count=0,
    )
    await db.execute(
        """
        INSERT INTO sources (id, name, kind, url, enabled, config_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            new.id,
            new.name,
            new.kind.value,
            new.url,
            1 if new.enabled else 0,
            json.dumps(new.config_json),
        ),
    )
    await db.commit()
    return new


async def patch_source(
    source_id: str,
    *,
    name: Optional[str] = None,
    url: Optional[str] = None,
    enabled: Optional[bool] = None,
    config_json: Optional[dict[str, Any]] = None,
) -> Optional[Source]:
    """Update one or more fields of a source. Returns the updated Source."""
    db = get_db()
    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        args.append(name)
    if url is not None:
        sets.append("url = ?")
        args.append(url)
    if enabled is not None:
        sets.append("enabled = ?")
        args.append(1 if enabled else 0)
    if config_json is not None:
        sets.append("config_json = ?")
        args.append(json.dumps(config_json))
    if not sets:
        return await get_source(source_id)
    args.append(source_id)
    await db.execute(
        f"UPDATE sources SET {', '.join(sets)} WHERE id = ?",
        tuple(args),
    )
    await db.commit()
    return await get_source(source_id)


async def delete_source(source_id: str) -> bool:
    db = get_db()
    cur = await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    await db.commit()
    return cur.rowcount > 0


# ----- Webhooks (v0.3) ----------------------------------------------------

_WEBHOOK_COLS = (
    "id, url, secret, source_id, tag, enabled, fail_streak, "
    "last_status, last_delivered_at, created_at"
)


def _row_to_webhook(row: tuple) -> Webhook:
    (wid, url, secret, source_id, tag, enabled, fail_streak,
     last_status, last_delivered_at, created_at) = row
    return Webhook(
        id=wid,
        url=url,
        secret=secret,
        source_id=source_id,
        tag=tag,
        enabled=bool(enabled),
        fail_streak=int(fail_streak or 0),
        last_status=last_status,
        last_delivered_at=_parse_iso(last_delivered_at),
        created_at=_parse_iso(created_at),
    )


async def create_webhook(
    url: str,
    secret: str,
    *,
    source_id: Optional[str] = None,
    tag: Optional[str] = None,
) -> Webhook:
    db = get_db()
    wid = _new_id("wh")
    await db.execute(
        "INSERT INTO webhooks (id, url, secret, source_id, tag, created_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (wid, url, secret, source_id, tag),
    )
    await db.commit()
    got = await get_webhook(wid)
    assert got is not None
    return got


async def get_webhook(webhook_id: str) -> Optional[Webhook]:
    db = get_db()
    cur = await db.execute(
        f"SELECT {_WEBHOOK_COLS} FROM webhooks WHERE id = ?", (webhook_id,)
    )
    row = await cur.fetchone()
    return _row_to_webhook(row) if row else None


async def list_webhooks() -> list[Webhook]:
    db = get_db()
    cur = await db.execute(
        f"SELECT {_WEBHOOK_COLS} FROM webhooks ORDER BY created_at ASC"
    )
    return [_row_to_webhook(r) for r in await cur.fetchall()]


async def list_enabled_webhooks() -> list[Webhook]:
    db = get_db()
    cur = await db.execute(
        f"SELECT {_WEBHOOK_COLS} FROM webhooks WHERE enabled = 1 ORDER BY created_at ASC"
    )
    return [_row_to_webhook(r) for r in await cur.fetchall()]


async def set_webhook_enabled(webhook_id: str, enabled: bool) -> Optional[Webhook]:
    db = get_db()
    cur = await db.execute(
        "UPDATE webhooks SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, webhook_id),
    )
    await db.commit()
    if cur.rowcount == 0:
        return None
    return await get_webhook(webhook_id)


async def record_webhook_delivery(
    webhook_id: str,
    *,
    ok: bool,
    status: str,
    max_fails: int,
) -> None:
    """Record a delivery attempt. On success reset the streak; on failure
    bump it and auto-disable once it reaches ``max_fails``."""
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    if ok:
        await db.execute(
            "UPDATE webhooks SET fail_streak = 0, last_status = ?, "
            "last_delivered_at = ? WHERE id = ?",
            (status, now_iso, webhook_id),
        )
    else:
        await db.execute(
            "UPDATE webhooks SET fail_streak = fail_streak + 1, last_status = ?, "
            "last_delivered_at = ?, "
            "enabled = CASE WHEN fail_streak + 1 >= ? THEN 0 ELSE enabled END "
            "WHERE id = ?",
            (status, now_iso, max_fails, webhook_id),
        )
    await db.commit()


async def mark_source_synced(source_id: str, synced_at_iso: str, last_error: Optional[str]) -> None:
    db = get_db()
    await db.execute(
        "UPDATE sources SET last_synced_at = ?, last_error = ? WHERE id = ?",
        (synced_at_iso, last_error, source_id),
    )
    await db.commit()


async def mark_source_error(source_id: str, error: str) -> None:
    db = get_db()
    await db.execute(
        "UPDATE sources SET last_error = ? WHERE id = ?",
        (error[:500], source_id),
    )
    await db.commit()


# ----- Items --------------------------------------------------------------

async def list_items(
    source_id: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[KnowledgeItem]:
    db = get_db()
    where: list[str] = []
    args: list[Any] = []

    # When `q` is supplied we go through the FTS5 index. That gives us:
    #   - prefix matching (typing "and" finds "Andreas")
    #   - real index usage (no LIKE '%x%' table scan)
    #   - Chinese single-character tokenization that still beats LIKE
    #     on substring match by a wide margin
    # The FTS path is opt-in: an empty / whitespace-only query
    # falls back to the original non-FTS code path below, which
    # is what `GET /api/items` with no `?q=` does for the inbox's
    # default render.
    fts_match: Optional[str] = None
    if q:
        # Imported lazily so this module stays import-cheap for the
        # tests that don't care about FTS.
        from prism_sidecar.fts5 import sanitize_fts5_query

        fts_match = sanitize_fts5_query(q)

    if fts_match is not None:
        # FTS5 path: one JOIN across items_fts → items → sources,
        # ordered by FTS rank. Every column reference is qualified,
        # so the "ambiguous rowid" trap the pre-v3 two-query version
        # worked around doesn't apply.
        #
        # The source_id / status filters are applied HERE too — the
        # pre-v3 version silently dropped them on the FTS path, so
        # searching with a source or status filter active returned
        # unfiltered results (the inbox sends all three together).
        where = ["items_fts MATCH ?"]
        args = [fts_match]
        if source_id:
            where.append("i.source_id = ?")
            args.append(source_id)
        if status and status != "all":
            where.append("i.status = ?")
            args.append(status)
        sql = f"""
            SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
                   i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
                   i.published_at, i.fetched_at, i.distilled_at, i.status,
                   i.content_type, i.duration_sec, i.metadata_json,
                   s.name AS source_name
            FROM items_fts
            JOIN items i ON i.rowid = items_fts.rowid
            JOIN sources s ON s.id = i.source_id
            WHERE {' AND '.join(where)}
            ORDER BY items_fts.rank
            LIMIT ? OFFSET ?
        """
        args.extend([int(limit), int(offset)])
        cur = await db.execute(sql, tuple(args))
        rows = await cur.fetchall()
        items: list[KnowledgeItem] = []
        for row in rows:
            source_name = row[-1]
            item_row = row[:-1]
            items.append(_row_to_item(item_row, source_name=source_name))
        return items

    # Non-FTS path: existing source / status filters, no full-text
    # search. Kept identical to v0.2a so the inbox's default render
    # (no `?q=`) doesn't pay the FTS query-plan cost.
    if source_id:
        where.append("i.source_id = ?")
        args.append(source_id)
    if status and status != "all":
        where.append("i.status = ?")
        args.append(status)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
               i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
               i.published_at, i.fetched_at, i.distilled_at, i.status,
               i.content_type, i.duration_sec, i.metadata_json,
               s.name AS source_name
        FROM items i
        JOIN sources s ON s.id = i.source_id
        {where_sql}
        ORDER BY i.published_at DESC
        LIMIT ? OFFSET ?
    """
    # bind limit/offset at the end
    args.extend([int(limit), int(offset)])

    cur = await db.execute(sql, tuple(args))
    rows = await cur.fetchall()
    items: list[KnowledgeItem] = []
    for row in rows:
        # row has 18 fields when source_name join is included
        source_name = row[-1]
        item_row = row[:-1]
        items.append(_row_to_item(item_row, source_name=source_name))
    return items


async def get_item(item_id: str) -> Optional[KnowledgeItem]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
               i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
               i.published_at, i.fetched_at, i.distilled_at, i.status,
               i.content_type, i.duration_sec, i.metadata_json,
               s.name AS source_name
        FROM items i
        JOIN sources s ON s.id = i.source_id
        WHERE i.id = ?
        """,
        (item_id,),
    )
    row = await cur.fetchone()
    if not row:
        return None
    source_name = row[-1]
    return _row_to_item(row[:-1], source_name=source_name)


async def item_exists_by_url(url: str) -> bool:
    db = get_db()
    cur = await db.execute("SELECT 1 FROM items WHERE url = ? LIMIT 1", (url,))
    return (await cur.fetchone()) is not None


async def insert_item_from_raw(source: Source, raw: RawItem) -> str:
    """Insert a fresh item from a RawItem. Returns the new item id.

    The item is stored with empty zh fields; the distiller fills them in
    a separate call (so a failed distiller doesn't lose the raw data).
    """
    db = get_db()
    item_id = _new_id("itm")
    now = datetime.now(timezone.utc)
    await db.execute(
        """
        INSERT INTO items (
            id, source_id, url, title_en, title_zh, summary_en, summary_zh,
            key_points_zh, tags_zh, author, published_at, fetched_at,
            distilled_at, status, content_type, duration_sec, metadata_json
        ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?,
                  NULL, 'unread', ?, ?, ?)
        """,
        (
            item_id,
            source.id,
            raw.url,
            raw.title,
            raw.author,
            raw.published_at.astimezone(timezone.utc).isoformat() if raw.published_at.tzinfo else raw.published_at.replace(tzinfo=timezone.utc).isoformat(),
            now.isoformat(),
            raw.content_type.value,
            raw.duration_sec,
            json.dumps(raw.metadata or {}),
        ),
    )
    # Keep an English summary placeholder if content exists, so the UI has
    # something to render before distillation.
    if raw.content:
        en_summary = raw.content[:280].replace("\n", " ")
        await db.execute(
            "UPDATE items SET summary_en = ? WHERE id = ?",
            (en_summary, item_id),
        )
    # Schema v3: write the (CJK-segmented) FTS index row. Done after
    # the summary placeholder so the index sees the final values.
    await _fts_upsert(db, item_id)
    await db.commit()
    return item_id


async def update_item_distilled(item_id: str, distilled: DistilledItem) -> None:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        UPDATE items
        SET title_zh = ?, summary_zh = ?, key_points_zh = ?, tags_zh = ?,
            distilled_at = ?
        WHERE id = ?
        """,
        (
            distilled.title_zh,
            distilled.summary_zh,
            json.dumps(distilled.key_points_zh, ensure_ascii=False),
            json.dumps(distilled.tags_zh, ensure_ascii=False),
            now,
            item_id,
        ),
    )
    # Schema v3: refresh the FTS index row with the new zh fields.
    await _fts_upsert(db, item_id)
    await db.commit()


async def update_item_status(item_id: str, status: ItemStatus) -> Optional[KnowledgeItem]:
    """Set an item's read/starred/archived status. Returns the updated item.

    `status` is not FTS-indexed, so no index maintenance is needed here.
    """
    db = get_db()
    cur = await db.execute(
        "UPDATE items SET status = ? WHERE id = ?",
        (status.value, item_id),
    )
    await db.commit()
    if cur.rowcount == 0:
        return None
    return await get_item(item_id)


# ----- Sync jobs / history ------------------------------------------------

async def create_job(source_id: Optional[str], *, sources_total: int = 0) -> str:
    """Create a sync_jobs row and return the job_id.

    ``sources_total`` is written at creation time so a client polling
    GET /api/sync/{job_id} mid-run sees "done/total" instead of "0/0".
    Every caller knows its source list before creating the job; the
    column used to be written only by `finish_job`, which made the
    running-state response lie until the job ended.
    """
    db = get_db()
    job_id = _new_id("job")
    await db.execute(
        """
        INSERT INTO sync_jobs (job_id, source_id, status, started_at, sources_total)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (job_id, source_id, datetime.now(timezone.utc).isoformat(), sources_total),
    )
    await db.commit()
    return job_id


async def finish_job(
    job_id: str,
    *,
    status: SyncJobStatus,
    items_new: int = 0,
    items_distilled: int = 0,
    sources_total: int = 0,
    sources_done: int = 0,
    error: Optional[str] = None,
) -> None:
    db = get_db()
    await db.execute(
        """
        UPDATE sync_jobs
        SET status = ?, finished_at = ?, items_new = ?, items_distilled = ?,
            sources_total = ?, sources_done = ?, error = ?
        WHERE job_id = ?
        """,
        (
            status.value,
            datetime.now(timezone.utc).isoformat(),
            items_new,
            items_distilled,
            sources_total,
            sources_done,
            error,
            job_id,
        ),
    )
    await db.commit()


async def update_job_progress(
    job_id: str,
    *,
    items_new: int,
    items_distilled: int,
    sources_done: int,
) -> None:
    """Update a job's running counters."""
    db = get_db()
    await db.execute(
        """
        UPDATE sync_jobs
        SET items_new = ?, items_distilled = ?, sources_done = ?
        WHERE job_id = ?
        """,
        (items_new, items_distilled, sources_done, job_id),
    )
    await db.commit()


async def get_job(job_id: str) -> Optional[SyncResult]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT job_id, source_id, status, started_at, finished_at,
               items_new, items_distilled, sources_total, sources_done, error
        FROM sync_jobs WHERE job_id = ?
        """,
        (job_id,),
    )
    row = await cur.fetchone()
    if not row:
        return None
    (
        jid, sid, status, started, finished,
        new_n, distilled_n, total, done, err,
    ) = row
    return SyncResult(
        job_id=jid,
        source_id=sid,  # type: ignore[arg-type]
        started_at=_parse_iso(started) or datetime.now(timezone.utc),
        finished_at=_parse_iso(finished),
        status=SyncJobStatus(status),
        items_new=int(new_n or 0),
        items_distilled=int(distilled_n or 0),
        sources_total=int(total or 0),
        sources_done=int(done or 0),
        error=err,
    )


async def is_any_job_running() -> bool:
    """True if any sync_jobs row is currently in 'running' state."""
    db = get_db()
    cur = await db.execute(
        "SELECT 1 FROM sync_jobs WHERE status = 'running' LIMIT 1"
    )
    return (await cur.fetchone()) is not None


async def fail_orphan_running_jobs() -> int:
    """Mark leftover 'running' jobs as errored. Returns the count fixed.

    Called once at startup (lifespan). If the sidecar crashed or was
    killed mid-sync, its job row stays 'running' forever — and because
    `is_any_job_running()` treats that row as an active sync, every
    subsequent /api/sync would 409 until the DB was hand-edited. No
    such job can actually be running at startup (jobs live only in
    this process), so flipping them to 'error' is always safe.
    """
    db = get_db()
    cur = await db.execute(
        """
        UPDATE sync_jobs
        SET status = 'error', finished_at = ?,
            error = 'orphaned: sidecar exited mid-run'
        WHERE status = 'running'
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    await db.commit()
    if cur.rowcount:
        log.warning(
            "[store] marked %d orphaned running sync job(s) as error", cur.rowcount
        )
    return cur.rowcount


async def write_sync_log(
    *,
    source_id: Optional[str],
    job_id: Optional[str],
    started_at: str,
    finished_at: Optional[str],
    items_new: int,
    items_distilled: int,
    error: Optional[str],
) -> None:
    db = get_db()
    await db.execute(
        """
        INSERT INTO sync_log
            (source_id, job_id, started_at, finished_at, items_new, items_distilled, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, job_id, started_at, finished_at, items_new, items_distilled, error),
    )
    await db.commit()


async def list_sync_history(limit: int = 10) -> list[SyncLogEntry]:
    db = get_db()
    cur = await db.execute(
        """
        SELECT id, source_id, started_at, finished_at, items_new, items_distilled, error
        FROM sync_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = await cur.fetchall()
    out: list[SyncLogEntry] = []
    for row in rows:
        (log_id, sid, started, finished, new_n, distilled_n, err) = row
        out.append(
            SyncLogEntry(
                id=int(log_id),
                source_id=sid,
                started_at=_parse_iso(started) or datetime.now(timezone.utc),
                finished_at=_parse_iso(finished),
                items_new=int(new_n or 0),
                items_distilled=int(distilled_n or 0),
                error=err,
            )
        )
    return out


# ----- First-run bootstrap ------------------------------------------------

async def ensure_default_sources(seeds: list[dict[str, Any]]) -> None:
    """Insert the default seed sources if `sources` is empty.

    Idempotent — does nothing if any source already exists.
    """
    db = get_db()
    cur = await db.execute("SELECT COUNT(*) FROM sources")
    count = (await cur.fetchone())[0]
    if count > 0:
        return
    for seed in seeds:
        await db.execute(
            """
            INSERT INTO sources (id, name, kind, url, enabled, config_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), datetime('now')))
            """,
            (
                seed["id"],
                seed["name"],
                seed["kind"],
                seed["url"],
                1 if seed.get("enabled", True) else 0,
                json.dumps(seed.get("config_json", {})),
                seed.get("created_at", ""),
            ),
        )
    await db.commit()


# ----- _meta key/value (e.g. first-sync flags) -------------------------

async def get_meta(key: str) -> Optional[str]:
    db = get_db()
    cur = await db.execute("SELECT value FROM _meta WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row[0] if row else None


async def set_meta(key: str, value: str) -> None:
    db = get_db()
    await db.execute(
        "INSERT INTO _meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await db.commit()


async def has_meta(key: str) -> bool:
    return await get_meta(key) is not None


__all__ = [
    "create_source",
    "get_source",
    "list_sources",
    "patch_source",
    "delete_source",
    "mark_source_synced",
    "mark_source_error",
    "list_items",
    "get_item",
    "item_exists_by_url",
    "insert_item_from_raw",
    "update_item_distilled",
    "update_item_status",
    "create_job",
    "finish_job",
    "update_job_progress",
    "get_job",
    "is_any_job_running",
    "fail_orphan_running_jobs",
    "write_sync_log",
    "list_sync_history",
    "ensure_default_sources",
    "health_snapshot",
    "get_meta",
    "set_meta",
    "has_meta",
]
