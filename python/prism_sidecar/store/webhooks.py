"""Webhooks (v0.3) — registration rows + delivery bookkeeping.

Delivery itself (HMAC signing, SSRF guard, HTTP POST) lives in
`prism_sidecar.webhooks`; this module is only the persistence layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from prism_sidecar.db import get_db
from prism_sidecar.models import Webhook
from prism_sidecar.store._shared import _new_id, _parse_iso

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
