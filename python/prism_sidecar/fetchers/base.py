"""Fetcher Protocol + RawItem.

A `Fetcher` takes a `Source` and returns a list of `RawItem`. Each raw item
is an unprocessed representation of a single article / post; the
distillation step turns it into a `KnowledgeItem` with Chinese title /
summary / tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from prism_sidecar.models import ContentType, Source, SourceKind


@dataclass(slots=True)
class RawItem:
    """One item pulled from a source, before LLM distillation."""

    url: str
    title: str
    content: str
    published_at: datetime
    author: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_type: ContentType = ContentType.article
    duration_sec: int | None = None


class FetchError(Exception):
    """The whole source is unusable for this sync run (v0.2c contract).

    Raised by fetchers when the *source itself* can't be fetched: DNS
    failure, the listing endpoint 4xx/5xx after retries, a required
    library missing, or an unusable source config. Per-ITEM failures
    (one video's subtitle didn't download) must NOT raise — skip the
    item, log, keep going.

    `retryable=False` marks errors that won't fix themselves (bad
    config, missing dependency) — the scheduler's cooldown/retry logic
    treats them as "wait for the user", not "try again in 2h".

    `partial_items` carries whatever the fetcher managed to build
    before dying, so the pipeline can still insert them instead of
    throwing away completed work.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        partial_items: list["RawItem"] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.partial_items: list[RawItem] = list(partial_items or [])


@runtime_checkable
class Fetcher(Protocol):
    """A pluggable fetcher for a given SourceKind."""

    kind: SourceKind

    async def fetch(
        self, source: Source, *, lookback_days: int | None = None
    ) -> list[RawItem]:
        """Fetch raw items from the given source.

        `lookback_days` is passed by the pipeline on every call (wider on a
        source's first sync, narrower afterwards — see
        `pipeline/sync.py::_lookback_for_source`). Every implementation MUST
        accept this keyword even if it has no meaningful lookback window
        (e.g. HackerNewsFetcher, which fetches all-time and dedupes by
        objectID) — silently ignoring the value is fine, but the parameter
        itself must exist or the pipeline call raises `TypeError`.

        Error contract (v0.2c — changed from "never raise"):

        * WHOLE-SOURCE failure (listing endpoint dead after retries,
          required lib missing, unusable config) → raise `FetchError`,
          attaching any already-built items via `partial_items`. The
          pipeline inserts the partials, records `sources.last_error`,
          and feeds the scheduler's failure-cooldown state.
        * PER-ITEM failure (one entry / one video broke) → log, skip
          that item, keep going. Do not raise.
        * Raising anything other than `FetchError` is treated by the
          pipeline as a fetcher bug (still caught, still recorded, but
          logged at exception level).

        The old contract ("log and return []") hid real outages: the
        pipeline couldn't tell "no new posts today" from "the feed has
        been 500ing for a week" — `sources.last_error` was effectively
        dead code. See docs/design/retry-and-rate-limit.md §2.
        """
        ...


__all__ = ["Fetcher", "FetchError", "RawItem"]
