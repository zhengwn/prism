"""Sources CRUD + sync-status columns + first-run seeding."""

from __future__ import annotations

import json
from typing import Any, Optional

from prism_sidecar.db import get_db
from prism_sidecar.models import Source, SourceKind
from prism_sidecar.store._shared import _new_id, _parse_iso


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
    # Re-read so the response carries the DB truth — most notably the
    # `created_at` the INSERT just generated (the in-memory `new` model
    # had None, so POST /api/sources returned createdAt=null).
    created = await get_source(new.id)
    return created if created is not None else new


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
