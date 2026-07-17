"""Items: list/get (FTS5-aware), insert-from-raw, distill/status updates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from prism_sidecar.db import get_db
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ItemStatus, KnowledgeItem, Source
from prism_sidecar.store._shared import _USER_TAGS_SELECT, _new_id, _row_to_item


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


async def list_items(
    source_id: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
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
        if tag:
            where.append("EXISTS (SELECT 1 FROM item_tags WHERE item_id = i.id AND tag = ?)")
            args.append(tag)
        sql = f"""
            SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
                   i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
                   i.published_at, i.fetched_at, i.distilled_at, i.status,
                   i.content_type, i.duration_sec, i.metadata_json,
                   {_USER_TAGS_SELECT},
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
            user_tags = row[-2]
            item_row = row[:-2]
            items.append(_row_to_item(item_row, source_name=source_name, user_tags=user_tags))
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
    if tag:
        where.append("EXISTS (SELECT 1 FROM item_tags WHERE item_id = i.id AND tag = ?)")
        args.append(tag)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
               i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
               i.published_at, i.fetched_at, i.distilled_at, i.status,
               i.content_type, i.duration_sec, i.metadata_json,
               {_USER_TAGS_SELECT},
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
        # row = [17 item fields, user_tags, source_name]
        source_name = row[-1]
        user_tags = row[-2]
        item_row = row[:-2]
        items.append(_row_to_item(item_row, source_name=source_name, user_tags=user_tags))
    return items


async def get_item(item_id: str) -> Optional[KnowledgeItem]:
    db = get_db()
    cur = await db.execute(
        f"""
        SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
               i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
               i.published_at, i.fetched_at, i.distilled_at, i.status,
               i.content_type, i.duration_sec, i.metadata_json,
               {_USER_TAGS_SELECT},
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
    user_tags = row[-2]
    return _row_to_item(row[:-2], source_name=source_name, user_tags=user_tags)


async def item_exists_by_url(url: str) -> bool:
    db = get_db()
    cur = await db.execute("SELECT 1 FROM items WHERE url = ? LIMIT 1", (url,))
    return (await cur.fetchone()) is not None


# Cap for the stored raw content (schema v6). Big enough for any real
# subtitle transcript (a 2h video's cues run ~60-100k chars); the cap only
# guards against a pathological feed embedding megabytes in one entry.
_MAX_RAW_CONTENT_LEN = 200_000


async def insert_item_from_raw(source: Source, raw: RawItem) -> str:
    """Insert a fresh item from a RawItem. Returns the new item id.

    The item is stored with empty zh fields; the distiller fills them in
    a separate call (so a failed distiller doesn't lose the raw data).

    Schema v6: the raw content itself is persisted (truncated) so a later
    redistill can re-prompt from the full source text instead of the
    280-char summary placeholder — see `get_item_content`.
    """
    db = get_db()
    item_id = _new_id("itm")
    now = datetime.now(timezone.utc)
    await db.execute(
        """
        INSERT INTO items (
            id, source_id, url, title_en, title_zh, summary_en, summary_zh,
            key_points_zh, tags_zh, author, published_at, fetched_at,
            distilled_at, status, content_type, duration_sec, metadata_json,
            content
        ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?,
                  NULL, 'unread', ?, ?, ?, ?)
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
            (raw.content or "")[:_MAX_RAW_CONTENT_LEN] or None,
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


async def get_item_content(item_id: str) -> Optional[str]:
    """The stored raw content for one item (schema v6), or None.

    Deliberately NOT part of KnowledgeItem / the REST list responses —
    the column can be 100k+ chars per row and the only consumer is the
    redistill pipeline, which fetches it per item on demand.
    """
    db = get_db()
    cur = await db.execute("SELECT content FROM items WHERE id = ?", (item_id,))
    row = await cur.fetchone()
    return row[0] if row and row[0] else None


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
