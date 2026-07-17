"""Semantic-search vectors (v0.5) — sqlite-vec KNN over item embeddings."""

from __future__ import annotations

import struct
from typing import Any, Optional

from prism_sidecar.db import get_db, vec_available
from prism_sidecar.models import KnowledgeItem
from prism_sidecar.store._shared import _USER_TAGS_SELECT, _row_to_item


def _serialize_vec(vec: list[float]) -> bytes:
    """Pack a float vector into the little-endian float32 blob vec0 wants."""
    return struct.pack(f"<{len(vec)}f", *vec)


async def upsert_item_vector(item_id: str, vec: list[float]) -> None:
    """(Re)write an item's embedding row in `items_vec`. No-op if the vector
    table isn't available (sqlite-vec didn't load)."""
    if not vec_available():
        return
    db = get_db()
    blob = _serialize_vec(vec)
    # vec0 has no UPSERT; delete-then-insert keeps re-embeds idempotent.
    await db.execute("DELETE FROM items_vec WHERE item_id = ?", (item_id,))
    await db.execute(
        "INSERT INTO items_vec(item_id, embedding) VALUES (?, ?)", (item_id, blob)
    )
    await db.commit()


async def count_vectors() -> int:
    """How many items currently have an embedding indexed."""
    if not vec_available():
        return 0
    db = get_db()
    cur = await db.execute("SELECT COUNT(*) FROM items_vec")
    return int((await cur.fetchone())[0])


async def items_missing_vectors(limit: Optional[int] = None) -> list[tuple[str, str]]:
    """Distilled items with no embedding yet: (item_id, text_to_embed).

    Only distilled items are indexed — they have the Chinese title/summary
    that make good embedding text. The text prefers the distilled zh
    fields, falling back to the English originals.
    """
    if not vec_available():
        return []
    db = get_db()
    sql = (
        "SELECT id, "
        "TRIM(COALESCE(title_zh, title_en, '') || ' ' || "
        "COALESCE(summary_zh, summary_en, '')) AS text "
        "FROM items "
        "WHERE distilled_at IS NOT NULL "
        "AND id NOT IN (SELECT item_id FROM items_vec) "
        "ORDER BY published_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        cur = await db.execute(sql, (int(limit),))
    else:
        cur = await db.execute(sql)
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def count_items_missing_vectors() -> int:
    """How many distilled items still need an embedding."""
    if not vec_available():
        return 0
    db = get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM items WHERE distilled_at IS NOT NULL "
        "AND id NOT IN (SELECT item_id FROM items_vec)"
    )
    return int((await cur.fetchone())[0])


async def semantic_search(
    query_vec: list[float],
    limit: int = 30,
    source_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[KnowledgeItem]:
    """K-nearest items to `query_vec`, newest-filters applied, closest first.

    Over-fetches from the vec0 KNN (which can't apply the source/status
    filters itself), then joins to items, filters, and re-limits — so a
    tight source/status filter still returns up to `limit` real hits.
    """
    if not vec_available():
        return []
    db = get_db()
    knn_k = max(int(limit) * 4, int(limit) + 20)
    where = ["1 = 1"]
    args: list[Any] = [_serialize_vec(query_vec), knn_k]
    if source_id:
        where.append("i.source_id = ?")
        args.append(source_id)
    if status and status != "all":
        where.append("i.status = ?")
        args.append(status)
    sql = f"""
        WITH knn AS (
            SELECT item_id, distance FROM items_vec
            WHERE embedding MATCH ? ORDER BY distance LIMIT ?
        )
        SELECT i.id, i.source_id, i.url, i.title_en, i.title_zh, i.summary_en,
               i.summary_zh, i.key_points_zh, i.tags_zh, i.author,
               i.published_at, i.fetched_at, i.distilled_at, i.status,
               i.content_type, i.duration_sec, i.metadata_json,
               {_USER_TAGS_SELECT},
               s.name AS source_name
        FROM knn
        JOIN items i ON i.id = knn.item_id
        JOIN sources s ON s.id = i.source_id
        WHERE {' AND '.join(where)}
        ORDER BY knn.distance
        LIMIT ?
    """
    args.append(int(limit))
    cur = await db.execute(sql, tuple(args))
    rows = await cur.fetchall()
    items: list[KnowledgeItem] = []
    for row in rows:
        source_name = row[-1]
        user_tags = row[-2]
        item_row = row[:-2]
        items.append(_row_to_item(item_row, source_name=source_name, user_tags=user_tags))
    return items
