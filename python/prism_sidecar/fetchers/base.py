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


@runtime_checkable
class Fetcher(Protocol):
    """A pluggable fetcher for a given SourceKind."""

    kind: SourceKind

    async def fetch(self, source: Source) -> list[RawItem]:
        """Fetch raw items from the given source.

        Implementations MUST NOT raise on transient network / parse errors —
        they should log and return an empty list (or a partial list) so the
        pipeline can keep going. The pipeline writes the per-source error
        into `sources.last_error`.
        """
        ...


__all__ = ["Fetcher", "RawItem"]
