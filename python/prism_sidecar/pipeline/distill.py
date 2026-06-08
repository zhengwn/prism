"""Distill pipeline.

`redistill_all_pending()` walks every `items` row with `distilled_at IS NULL`
and re-runs the distiller on it. Used by the Settings → "重蒸馏所有 pending"
button. Two reasons a row ends up pending:

  - the user had no key configured when the item was first fetched
  - the user's key expired / ran out / was revoked mid-distillation
  - a transient LLM error happened (network blip, rate limit, etc.)

If a key invalid error comes back mid-batch we stop the whole batch
immediately — there's no point hammering a dead key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from prism_sidecar.config import is_distiller_configured
from prism_sidecar.distillers.base import (
    DistilledItem,
    Distiller,
    DistillerKeyInvalid,
    DistillerNotConfigured,
)
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.store import (
    get_item,
    get_source,
    update_item_distilled,
)
from prism_sidecar.db import get_db

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RedistillResult:
    started_pending: int = 0
    distilled: int = 0
    failed: int = 0
    key_invalid: bool = False
    error: Optional[str] = None
    # A small sample of the items that still failed, for surfacing in the UI.
    sample_failures: list[str] = field(default_factory=list)


async def list_pending_distill_ids() -> list[str]:
    """Return IDs of all items that have not been distilled yet."""
    db = get_db()
    cur = await db.execute(
        "SELECT id FROM items WHERE distilled_at IS NULL ORDER BY fetched_at ASC"
    )
    rows = await cur.fetchall()
    return [r[0] for r in rows]


def _get_distiller() -> Distiller | None:
    if not is_distiller_configured():
        return None
    return DeepSeekDistiller()


async def redistill_all_pending(
    distiller: Distiller | None = None,
    batch_limit: int = 1000,
) -> RedistillResult:
    """Re-run distillation on every pending item.

    `batch_limit` caps the run so a runaway backlog can't lock the
    sidecar for hours. Caller can re-invoke to do another pass.

    Returns stats describing what happened. Does NOT raise — caller's
    FastAPI route should map `key_invalid=True` to a 503-ish response.
    """
    result = RedistillResult()
    if distiller is None:
        distiller = _get_distiller()

    if distiller is None:
        result.error = "distiller_not_configured"
        return result

    pending_ids = await list_pending_distill_ids()
    result.started_pending = min(len(pending_ids), batch_limit)
    if not pending_ids:
        return result

    pending_ids = pending_ids[:batch_limit]

    for item_id in pending_ids:
        item = await get_item(item_id)
        if item is None or item.distilled_at is not None:
            # Someone distilled it between our list_pending_distill_ids()
            # call and now. Skip.
            continue

        # Rebuild a minimal RawItem from the stored item so we can re-prompt.
        # We only have title_en + url from the row (we don't keep raw content
        # in items). That's actually OK for DeepSeek — title + URL is enough
        # context to produce a decent summary.
        raw = RawItem(
            url=item.url,
            title=item.title_en,
            content=item.summary_en or item.title_en,  # best-effort
            published_at=item.published_at,
            author=item.author,
            metadata={},
            content_type=item.content_type,
        )

        try:
            distilled: DistilledItem = await distiller.distill(raw)
        except DistillerNotConfigured:
            result.error = "distiller_not_configured"
            return result
        except DistillerKeyInvalid as exc:
            result.key_invalid = True
            result.error = f"key_invalid: {exc}"
            log.error("[redistill] API key invalid, aborting batch: %s", exc)
            return result
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            if len(result.sample_failures) < 5:
                result.sample_failures.append(f"{item.url}: {exc!r}")
            log.warning("[redistill] failed for %s: %s", item.url, exc)
            continue

        try:
            await update_item_distilled(item.id, distilled)
            result.distilled += 1
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            log.warning("[redistill] DB write failed for %s: %s", item.id, exc)
            if len(result.sample_failures) < 5:
                result.sample_failures.append(f"{item.id}: {exc!r}")

    return result


__all__ = [
    "RedistillResult",
    "list_pending_distill_ids",
    "redistill_all_pending",
]
