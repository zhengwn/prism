"""_meta key/value store (e.g. first-sync flags)."""

from __future__ import annotations

from typing import Optional

from prism_sidecar.db import get_db


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
