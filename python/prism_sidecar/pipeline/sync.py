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
from datetime import datetime, timedelta, timezone
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
from prism_sidecar.fetchers.base import FetchError, RawItem
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


# ---- Failure cooldown (v0.2c) --------------------------------------------
#
# Stored in the meta table (same pattern as first_sync_done:*) so we
# don't need a db schema migration:
#   fail_streak:{source_id}  — consecutive whole-source fetch failures
#   retry_after:{source_id}  — ISO time before which the SCHEDULER
#                              should not retry (manual sync ignores it)

_COOLDOWN_CAP_HOURS = 24


def _fail_streak_key(source_id: str) -> str:
    return f"fail_streak:{source_id}"


def _retry_after_key(source_id: str) -> str:
    return f"retry_after:{source_id}"


async def get_fail_streak(source_id: str) -> int:
    raw = await get_meta(_fail_streak_key(source_id))
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


async def record_sync_failure(source_id: str, *, retryable: bool = True) -> int:
    """Bump the failure streak and set the cooldown window.

    Cooldown: min(2**streak, 24) hours — 2h, 4h, 8h, 16h, 24h, 24h…
    Non-retryable errors (bad config, missing lib) jump straight to the
    24h cap: retrying every 2h won't fix a missing dependency.
    Returns the new streak.
    """
    streak = await get_fail_streak(source_id) + 1
    await set_meta(_fail_streak_key(source_id), str(streak))
    hours = _COOLDOWN_CAP_HOURS if not retryable else min(2 ** streak, _COOLDOWN_CAP_HOURS)
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    await set_meta(_retry_after_key(source_id), until.isoformat())
    log.info(
        "[sync] source %s failure streak=%d, cooldown until %s",
        source_id, streak, until.isoformat(),
    )
    return streak


async def record_sync_success(source_id: str) -> None:
    """Clear the failure streak + cooldown after a successful fetch."""
    if await get_fail_streak(source_id) > 0:
        await set_meta(_fail_streak_key(source_id), "0")
        await set_meta(_retry_after_key(source_id), "")


async def source_in_cooldown(source_id: str) -> bool:
    """True if the scheduler should skip this source right now."""
    if await get_fail_streak(source_id) <= 0:
        return False
    raw = await get_meta(_retry_after_key(source_id))
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return datetime.now(timezone.utc) < until


async def source_retry_due(source_id: str) -> bool:
    """True if this source failed before AND its cooldown has expired.

    This is the hourly retry job's selection predicate — it only picks
    sources that are known-broken and due for another attempt.
    """
    if await get_fail_streak(source_id) <= 0:
        return False
    return not await source_in_cooldown(source_id)


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
    fetch_error: FetchError | None = None
    try:
        raw_items: list[RawItem] = await fetcher.fetch(source, lookback_days=lookback)
    except FetchError as exc:
        # Whole-source failure (v0.2c contract). Salvage whatever the
        # fetcher built before dying — the insert/distill loop below
        # still runs over the partials; the error is recorded at the end.
        log.error("[sync] %s (%s) fetch failed: %s", source.name, source.id, exc)
        fetch_error = exc
        raw_items = exc.partial_items
    except Exception as exc:  # noqa: BLE001
        # Not a FetchError → fetcher bug (e.g. the lookback_days
        # signature regression). Same accounting, louder log.
        log.exception("[sync] %s (%s) fetch raised unexpectedly", source.name, source.id)
        stats.error = f"fetch: {exc!r}"
        await mark_source_error(source.id, str(exc))
        await record_sync_failure(source.id)
        return stats

    stats.fetched = len(raw_items)

    new_item_ids: list[str] = []
    for raw in raw_items:
        try:
            existing = await item_exists_by_url(raw.url)
            if existing:
                continue

            item_id = await insert_item_from_raw(source, raw)
            stats.new_items += 1
            new_item_ids.append(item_id)

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
                    # Best-effort semantic-index update (no-op when
                    # embeddings / sqlite-vec aren't available).
                    from prism_sidecar import search
                    await search.embed_item(item_id)
                    stats.distilled += 1
                    await progress_store.item_succeeded()
                except DistillerNotConfigured:
                    # No key — stop trying for the rest of this run.
                    distiller = None
                except DistillerKeyInvalid as exc:
                    # Key is dead — stop DISTILLING (don't burn what
                    # little credit may remain) but KEEP INSERTING the
                    # remaining raw items. The old `break` here threw
                    # away every raw item after the failure point:
                    # they'd only reappear on a later sync if they
                    # were still inside the (now narrow, first-sync-
                    # window-already-consumed) lookback. Inserted-but-
                    # pending rows are recoverable via redistill.
                    stats.key_invalid = True
                    stats.error = f"key_invalid: {exc}"
                    log.error(
                        "[sync] %s: API key invalid, disabling distillation "
                        "for the rest of this run: %s",
                        source.name, exc,
                    )
                    distiller = None
                except Exception as exc:  # noqa: BLE001
                    stats.failed_distill += 1
                    await progress_store.item_failed()
                    log.warning(
                        "[sync] distill failed for %s: %s", raw.url, exc,
                    )
        except Exception as exc:  # noqa: BLE001
            log.exception("[sync] failed to process raw item %s: %s", raw.url, exc)
            continue

    # Whole-source fetch failure: record the error + bump the failure
    # streak, and do NOT consume the first-sync window (the wide lookback
    # should apply again once the source recovers). Partial items above
    # were still inserted/distilled — that work is kept.
    if fetch_error is not None:
        stats.error = f"fetch: {fetch_error}"
        await mark_source_error(source.id, str(fetch_error))
        await record_sync_failure(source.id, retryable=fetch_error.retryable)
        log.info(
            "[sync] %s: FAILED fetch, salvaged=%d new=%d distilled=%d",
            source.name, stats.fetched, stats.new_items, stats.distilled,
        )
        return stats

    # Mark the sync finished. The fetch itself succeeded, so the
    # first-sync window IS consumed — but keep any distill-level error
    # visible on the source instead of wiping it with None (the pre-fix
    # behaviour cleared last_error even when the run ended with
    # key_invalid).
    now_iso = datetime.now(timezone.utc).isoformat()
    await mark_source_synced(source.id, now_iso, last_error=stats.error)
    await _mark_first_sync_done(source)
    await record_sync_success(source.id)

    # v0.3: fan the new items out to any registered webhooks. Best-effort —
    # dispatch_for_items never raises, and it's a no-op (one cheap query)
    # when no webhooks are registered. Imported lazily to keep the sync
    # module's import graph free of httpx at load time.
    if new_item_ids:
        from prism_sidecar import webhooks

        await webhooks.dispatch_for_items(new_item_ids)

    log.info(
        "[sync] %s: fetched=%d new=%d distilled=%d failed_distill=%d lookback=%dd",
        source.name, stats.fetched, stats.new_items, stats.distilled,
        stats.failed_distill, lookback,
    )
    return stats


__all__ = [
    "run_source_sync",
    "SyncStats",
    "get_fail_streak",
    "record_sync_failure",
    "record_sync_success",
    "source_in_cooldown",
    "source_retry_due",
]
