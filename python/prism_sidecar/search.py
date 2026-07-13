"""Semantic search orchestration (v0.5).

Ties together the MiniMax embeddings client (`embeddings.py`) and the
sqlite-vec index (`store.py`):

* :func:`search_status` — is semantic search available, and how much is
  indexed / pending?
* :func:`reindex_missing` — embed every distilled item that has no vector
  yet and store it. Idempotent; safe to call repeatedly.
* :func:`embed_item` — embed one item (used to keep the index fresh as
  items are distilled).
* :func:`semantic_search` — embed a query and return the nearest items.

Availability degrades cleanly: with no MiniMax key OR no sqlite-vec, these
report `available=False` and callers fall back to FTS5.
"""

from __future__ import annotations

import logging

from prism_sidecar import embeddings as _emb
from prism_sidecar import store as _store
from prism_sidecar.db import vec_available
from prism_sidecar.models import KnowledgeItem

log = logging.getLogger(__name__)


def available() -> bool:
    """Semantic search needs both a MiniMax key and a loaded vector table."""
    return _emb.embeddings_available() and vec_available()


async def search_status() -> dict:
    """Snapshot for the UI: availability + indexed / pending counts."""
    return {
        "available": available(),
        "embeddings_configured": _emb.embeddings_available(),
        "vec_available": vec_available(),
        "indexed": await _store.count_vectors(),
        "pending": await _store.count_items_missing_vectors(),
    }


def _embed_text_for(title: str | None, summary: str | None) -> str:
    return f"{title or ''} {summary or ''}".strip()


async def embed_item(item_id: str) -> bool:
    """Embed a single distilled item and store its vector (best-effort).

    Returns True on success. Never raises — used on the distill hot path,
    where a failed embedding must not break distillation.
    """
    if not available():
        return False
    try:
        item = await _store.get_item(item_id)
        if item is None or item.distilled_at is None:
            return False
        text = _embed_text_for(item.title_zh or item.title_en, item.summary_zh or item.summary_en)
        if not text:
            return False
        [vec] = await _emb.embed_texts([text], kind="db")
        await _store.upsert_item_vector(item_id, vec)
        return True
    except Exception as e:  # pragma: no cover - network dependent
        log.warning("[prism-sidecar] embed_item(%s) failed: %s", item_id, e)
        return False


async def reindex_missing(batch_limit: int | None = None) -> dict:
    """Embed every distilled item that has no vector yet.

    `batch_limit` caps how many are processed in one call (None = all).
    Returns {available, indexed, failed, remaining}.
    """
    if not available():
        return {
            "available": False,
            "indexed": 0,
            "failed": 0,
            "remaining": 0,
            "reason": "no MiniMax key" if not _emb.embeddings_available() else "sqlite-vec unavailable",
        }

    missing = await _store.items_missing_vectors(limit=batch_limit)
    if not missing:
        return {"available": True, "indexed": 0, "failed": 0, "remaining": 0}

    ids = [m[0] for m in missing]
    texts = [m[1] for m in missing]
    indexed = 0
    failed = 0
    try:
        vectors = await _emb.embed_texts(texts, kind="db")
    except _emb.EmbeddingError as e:
        log.warning("[prism-sidecar] reindex embedding call failed: %s", e)
        return {
            "available": True,
            "indexed": 0,
            "failed": len(ids),
            "remaining": await _store.count_items_missing_vectors(),
            "error": str(e),
        }

    for item_id, vec in zip(ids, vectors):
        try:
            await _store.upsert_item_vector(item_id, vec)
            indexed += 1
        except Exception as e:  # pragma: no cover
            log.warning("[prism-sidecar] upsert vector for %s failed: %s", item_id, e)
            failed += 1

    return {
        "available": True,
        "indexed": indexed,
        "failed": failed,
        "remaining": await _store.count_items_missing_vectors(),
    }


async def semantic_search(
    query: str,
    limit: int = 30,
    source_id: str | None = None,
    status: str | None = None,
) -> list[KnowledgeItem]:
    """Embed the query and return the nearest indexed items. Empty list when
    unavailable (caller should fall back to FTS)."""
    if not available() or not query.strip():
        return []
    query_vec = await _emb.embed_query(query.strip())
    return await _store.semantic_search(
        query_vec, limit=limit, source_id=source_id, status=status
    )


__all__ = [
    "available",
    "search_status",
    "embed_item",
    "reindex_missing",
    "semantic_search",
]
