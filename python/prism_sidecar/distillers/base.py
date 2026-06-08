"""Distiller Protocol + DistilledItem.

A `Distiller` takes a `RawItem` and returns a `DistilledItem` containing
the Chinese-language title, summary, key points, and tags. We use a
Protocol so we can swap providers (DeepSeek / OpenAI / local) without
touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from prism_sidecar.fetchers.base import RawItem


@dataclass(slots=True)
class DistilledItem:
    """The structured output of one distillation call."""

    title_zh: str
    summary_zh: str
    key_points_zh: list[str] = field(default_factory=list)
    tags_zh: list[str] = field(default_factory=list)


class DistillerNotConfigured(RuntimeError):
    """Raised when a Distiller is invoked without the required credentials."""


class DistillerKeyInvalid(RuntimeError):
    """Raised when the configured API key is rejected (401/403/quota/expired).

    Distinct from DistillerNotConfigured: a key is *present* but the
    provider says it's no good. Callers should stop the whole batch
    immediately (not retry) so we don't burn through what little credit
    the key may have left.
    """


@runtime_checkable
class Distiller(Protocol):
    """A pluggable LLM-backed distiller."""

    async def distill(self, raw: RawItem) -> DistilledItem:
        ...


__all__ = ["Distiller", "DistilledItem", "DistillerNotConfigured", "DistillerKeyInvalid"]
