"""User tags (v0.5) — manual labels on top of the distiller's auto tags."""

from __future__ import annotations

from typing import Optional

from prism_sidecar.db import get_db
from prism_sidecar.models import KnowledgeItem, TagCount
from prism_sidecar.store.items import get_item

_MAX_TAG_LEN = 50


def normalize_tag(tag: str) -> str:
    """Validate + normalize a user tag, or raise ValueError.

    Trims whitespace, rejects empty / over-long, and rejects control chars
    (which would corrupt the group_concat(tag, _TAG_SEP) round-trip used to
    ship tags in item rows). Tag matching is case-sensitive as typed.
    """
    t = (tag or "").strip()
    if not t:
        raise ValueError("tag must not be empty")
    if len(t) > _MAX_TAG_LEN:
        raise ValueError(f"tag too long (max {_MAX_TAG_LEN} characters)")
    if any(ord(c) < 0x20 for c in t):
        raise ValueError("tag must not contain control characters")
    return t


async def add_item_tag(item_id: str, tag: str) -> Optional[KnowledgeItem]:
    """Attach a user tag to an item. Idempotent. Returns the updated item,
    or None if the item doesn't exist. Raises ValueError on an invalid tag.

    User tags are not FTS-indexed (they're a filter dimension, not search
    text), so no index maintenance here.
    """
    clean = normalize_tag(tag)
    db = get_db()
    cur = await db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,))
    if await cur.fetchone() is None:
        return None
    await db.execute(
        "INSERT OR IGNORE INTO item_tags(item_id, tag) VALUES (?, ?)",
        (item_id, clean),
    )
    await db.commit()
    return await get_item(item_id)


async def remove_item_tag(item_id: str, tag: str) -> Optional[KnowledgeItem]:
    """Remove a user tag from an item. Idempotent (removing an absent tag is
    a no-op). Returns the updated item, or None if the item doesn't exist.
    """
    db = get_db()
    cur = await db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,))
    if await cur.fetchone() is None:
        return None
    await db.execute(
        "DELETE FROM item_tags WHERE item_id = ? AND tag = ?",
        (item_id, tag),
    )
    await db.commit()
    return await get_item(item_id)


async def list_user_tags() -> list[TagCount]:
    """All user tags with their item counts, most-used first."""
    db = get_db()
    cur = await db.execute(
        "SELECT tag, COUNT(*) AS n FROM item_tags "
        "GROUP BY tag ORDER BY n DESC, tag ASC"
    )
    rows = await cur.fetchall()
    return [TagCount(tag=r[0], count=int(r[1])) for r in rows]
