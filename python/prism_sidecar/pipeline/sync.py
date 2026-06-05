"""Sync pipeline.

`run_source_sync(source)` is the unit of work: it fetches the source,
deduplicates by `items.url`, inserts new raw rows, and (if a distiller is
configured) calls the LLM to fill in the bilingual fields.

The orchestrator (`run_all_sync` / `run_one_sync` in `app.py`) handles
job tracking and the per-source serialisation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from prism_sidecar.config import is_distiller_configured
from prism_sidecar.distillers.base import DistilledItem, Distiller, DistillerNotConfigured
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.fetchers import registry
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import Source
from prism_sidecar.store import (
    insert_item_from_raw,
    item_exists_by_url,
    mark_source_error,
    mark_source_synced,
    update_item_distilled,
)

log = logging.getLogger(__name__)


class SyncStats:
    """Return value of `run_source_sync`."""

    def __init__(self) -> None:
        self.fetched: int = 0
        self.new_items: int = 0
        self.distilled: int = 0
        self.failed_distill: int = 0
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "new_items": self.new_items,
            "distilled": self.distilled,
            "failed_distill": self.failed_distill,
            "error": self.error,
        }


def _get_distiller() -> Distiller | None:
    """Return a configured distiller, or None if API key missing.

    The distiller is lazily constructed so unit tests / dev runs without a
    key can still hit the fetch + insert path.
    """
    if not is_distiller_configured():
        return None
    return DeepSeekDistiller()


async def run_source_sync(source: Source, distiller: Distiller | None = None) -> SyncStats:
    """Fetch + dedupe + insert + distill for a single source.

    Errors at the fetch level are captured into the stats object and
    `sources.last_error` so the rest of the pipeline (and the UI) can
    surface them. We never raise — the caller (job orchestrator) needs to
    be able to continue with the next source.
    """
    stats = SyncStats()
    distiller = distiller if distiller is not None else _get_distiller()

    if not source.enabled:
        stats.error = "source disabled"
        return stats

    fetcher = registry.get_fetcher(source)
    try:
        raw_items: list[RawItem] = await fetcher.fetch(source)
    except Exception as exc:  # noqa: BLE001
        log.error("[sync] %s (%s) fetch raised: %s", source.name, source.id, exc)
        stats.error = f"fetch: {exc!r}"
        await mark_source_error(source.id, str(exc))
        return stats

    stats.fetched = len(raw_items)

    for raw in raw_items:
        try:
            existing = await item_exists_by_url(raw.url)
            if existing:
                continue

            item_id = await insert_item_from_raw(source, raw)
            stats.new_items += 1

            if distiller is not None:
                try:
                    distilled: DistilledItem = await distiller.distill(raw)
                    await update_item_distilled(item_id, distilled)
                    stats.distilled += 1
                except DistillerNotConfigured:
                    # No key — stop trying for the rest of this run.
                    distiller = None
                except Exception as exc:  # noqa: BLE001
                    stats.failed_distill += 1
                    log.warning(
                        "[sync] distill failed for %s: %s", raw.url, exc,
                    )
        except Exception as exc:  # noqa: BLE001
            log.exception("[sync] failed to process raw item %s: %s", raw.url, exc)
            continue

    # Mark success
    now_iso = datetime.now(timezone.utc).isoformat()
    await mark_source_synced(source.id, now_iso, last_error=None)

    log.info(
        "[sync] %s: fetched=%d new=%d distilled=%d failed_distill=%d",
        source.name, stats.fetched, stats.new_items, stats.distilled, stats.failed_distill,
    )
    return stats


__all__ = ["run_source_sync", "SyncStats"]
