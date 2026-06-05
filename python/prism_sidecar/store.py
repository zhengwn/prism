"""Data layer — in-memory for v0.1, swap to SQLite in v0.2."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from prism_sidecar.data.fixtures import ITEMS, SOURCES
from prism_sidecar.models import KnowledgeItem, Source

_lock = threading.RLock()
_started_at = datetime.now(timezone.utc)


def health_snapshot() -> dict:
    with _lock:
        return {
            "ok": True,
            "version": "0.1.0",
            "sources_count": len(SOURCES),
            "items_count": len(ITEMS),
            "uptime_sec": int((datetime.now(timezone.utc) - _started_at).total_seconds()),
        }


def list_sources() -> list[Source]:
    with _lock:
        return list(SOURCES)


def get_source(source_id: str) -> Optional[Source]:
    with _lock:
        return next((s for s in SOURCES if s.id == source_id), None)


def create_source(name: str, kind: str, url: str, enabled: bool = True) -> Source:
    with _lock:
        new = Source(
            id=f"src_{len(SOURCES) + 1:03d}",
            name=name,
            kind=kind,  # type: ignore[arg-type]
            url=url,
            enabled=enabled,
            item_count=0,
        )
        SOURCES.append(new)
        return new


def delete_source(source_id: str) -> bool:
    with _lock:
        for i, s in enumerate(SOURCES):
            if s.id == source_id:
                SOURCES.pop(i)
                return True
        return False


def list_items(
    source_id: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> list[KnowledgeItem]:
    with _lock:
        items = list(ITEMS)

    if source_id:
        items = [it for it in items if it.source_id == source_id]
    if status and status != "all":
        items = [it for it in items if it.status == status]
    if q:
        needle = q.lower()
        items = [
            it
            for it in items
            if needle in it.title.lower()
            or (it.summary and needle in it.summary.lower())
        ]

    # Newest first
    items.sort(key=lambda it: it.published_at, reverse=True)
    return items


def get_item(item_id: str) -> Optional[KnowledgeItem]:
    with _lock:
        return next((it for it in ITEMS if it.id == item_id), None)
