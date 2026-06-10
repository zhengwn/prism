"""Sync pipeline.

`run_source_sync(source)` is the unit of work: it fetches the source,
deduplicates by `items.url`, inserts new raw rows, and (if a distiller is
configured) calls the LLM to fill in the bilingual fields.

The orchestrator (`run_all_sync` / `run_one_sync` in `app.py`) handles
job tracking and the per-source serialisation.

First-sync behaviour: a source's *first* successful sync uses a wider
lookback window (INITIAL_FETCH_LOOKBACK_DAYS, default 30 days) so a fresh
install isn't sparse. After that, every sync uses FETCH_LOOKBACK_DAYS
(default 7 days) to keep daily runs light.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from prism_sidecar.config import (
    FETCH_LOOKBACK_DAYS,
    INITIAL_FETCH_LOOKBACK_DAYS,
    is_distiller_configured,
)
from prism_sidecar.distillers.base import (
    DistilledItem,
    Distiller,
    DistillerKeyInvalid,
    DistillerNotConfigured,
)
from prism_sidecar.distillers.registry import get_distiller
from prism_sidecar.fetchers import registry
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import Source
from prism_sidecar.progress import progress_store
from prism_sidecar.settings import (
    is_provider_configured,
    load_active_provider,
)
from prism_sidecar.store import (
    get_meta,
    insert_item_from_raw,
    item_exists_by_url,
    mark_source_error,
    mark_source_synced,
    set_meta,
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
        self.lookback_days: int = FETCH_LOOKBACK_DAYS
        self.error: Optional[str] = None
        self.key_invalid: bool = False

    def to_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "new_items": self.new_items,
            "distilled": self.distilled,
            "failed_distill": self.failed_distill,
            "lookback_days": self.lookback_days,
            "error": self.error,
            "key_invalid": self.key_invalid,
        }


def _get_distiller() -> Distiller | None:
    """Return a configured distiller, or None if API key missing.

    Reads the active provider from ``active_provider.json`` and asks
    the registry for the matching distiller class. The distiller is
    lazily constructed so unit tests / dev runs without a key can
    still hit the fetch + insert path.

    Note: the v0.1 ``is_distiller_configured()`` helper only checked
    the DeepSeek key. We now check the *active* provider's key, with
    a fallback to the old helper for any code paths that haven't been
    updated yet.
    """
    cfg = load_active_provider()
    provider = cfg["provider"]
    if not is_provider_configured(provider):
        return None
    try:
        return get_distiller(
            provider,
            model=cfg.get("model"),
            base_url=cfg.get("base_url"),
        )
    except (ValueError, Exception) as exc:  # noqa: BLE001
        log.warning("[sync] could not build distiller for %r: %s", provider, exc)
        return None


def _first_sync_done_key(source_id: str) -> str:
    return f"first_sync_done:{source_id}"


async def _lookback_for_source(source: Source) -> int:
    """Return the lookback window (days) to use for this source.

    Wider on the very first sync, normal afterwards.
    """
    if await get_meta(_first_sync_done_key(source.id)) is None:
        return INITIAL_FETCH_LOOKBACK_DAYS
    return FETCH_LOOKBACK_DAYS


async def _mark_first_sync_done(source: Source) -> None:
    await set_meta(_first_sync_done_key(source.id), datetime.now(timezone.utc).isoformat())


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

    lookback = await _lookback_for_source(source)
    stats.lookback_days = lookback

    fetcher = registry.get_fetcher(source)
    try:
        raw_items: list[RawItem] = await fetcher.fetch(source, lookback_days=lookback)
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
                # Per-item progress signal so the inbox progress bar
                # can show "正在蒸馏: <title>". This is the only
                # place in the sync pipeline that touches the
                # progress store — orchestrators in app.py are
                # responsible for the begin_run / end_run framing.
                await progress_store.item_started(
                    title=raw.title,
                    source=source.name,
                )
                try:
                    distilled: DistilledItem = await distiller.distill(raw)
                    await update_item_distilled(item_id, distilled)
                    stats.distilled += 1
                    await progress_store.item_succeeded()
                except DistillerNotConfigured:
                    # No key — stop trying for the rest of this run.
                    distiller = None
                except DistillerKeyInvalid as exc:
                    # Key is dead — bail out of the whole source so we
                    # don't burn what little credit may remain. Don't
                    # overwrite last_error here: let the orchestrator
                    # surface this to the UI.
                    stats.key_invalid = True
                    stats.error = f"key_invalid: {exc}"
                    log.error(
                        "[sync] %s: API key invalid, aborting distillation: %s",
                        source.name, exc,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    stats.failed_distill += 1
                    await progress_store.item_failed()
                    log.warning(
                        "[sync] distill failed for %s: %s", raw.url, exc,
                    )
        except Exception as exc:  # noqa: BLE001
            log.exception("[sync] failed to process raw item %s: %s", raw.url, exc)
            continue

    # Mark success
    now_iso = datetime.now(timezone.utc).isoformat()
    await mark_source_synced(source.id, now_iso, last_error=None)
    await _mark_first_sync_done(source)

    log.info(
        "[sync] %s: fetched=%d new=%d distilled=%d failed_distill=%d lookback=%dd",
        source.name, stats.fetched, stats.new_items, stats.distilled,
        stats.failed_distill, lookback,
    )
    return stats


__all__ = ["run_source_sync", "SyncStats"]
