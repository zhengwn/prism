"""Text embeddings via MiniMax (embo-01), for v0.5 semantic search.

MiniMax's embeddings endpoint is NOT OpenAI-compatible (litellm has no
handler for it), so we call it directly:

    POST {base}/embeddings
    { "model": "embo-01", "texts": [...], "type": "db" | "query" }
    -> { "vectors": [[...1536...], ...], "base_resp": { "status_code": 0 } }

`type` is asymmetric on purpose — MiniMax trains separate document ("db")
and "query" projections, so we embed stored items with "db" and search
queries with "query" for better retrieval.

Availability: semantic search only works when a MiniMax key is present in
the env (Tauri injects MINIMAX_API_KEY for the active provider). With no
key, `embeddings_available()` is False and callers fall back to FTS5.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

import httpx

from prism_sidecar import _http
from prism_sidecar import settings as _settings

log = logging.getLogger(__name__)

EMBED_MODEL = "embo-01"
# embo-01 returns fixed-width 1536-dim vectors. The vec0 table is created
# with this width, so it must match what the API returns.
EMBED_DIM = 1536
_DEFAULT_BASE = "https://api.minimaxi.com/v1"
# Cap texts per request. MiniMax accepts a batch; keep it modest so one
# failure doesn't sink a huge reindex and payloads stay small.
_BATCH = 32

EmbedKind = Literal["db", "query"]


class EmbeddingsUnavailable(RuntimeError):
    """Raised when no MiniMax key is configured (semantic search is off)."""


class EmbeddingError(RuntimeError):
    """A MiniMax embeddings call failed (network / API error)."""


def embeddings_available() -> bool:
    """True when a MiniMax key is in the env — the only thing embo-01 needs."""
    return bool(os.environ.get("MINIMAX_API_KEY"))


def _base_url() -> str:
    return _settings.resolve_base_url("minimax") or _DEFAULT_BASE


async def embed_texts(texts: list[str], *, kind: EmbedKind) -> list[list[float]]:
    """Embed a list of texts. `kind` is "db" (stored items) or "query".

    Returns one 1536-float vector per input, in order. Raises
    EmbeddingsUnavailable if no key, EmbeddingError on an API failure.
    """
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise EmbeddingsUnavailable("MINIMAX_API_KEY not set")
    if not texts:
        return []

    url = f"{_base_url()}/embeddings"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    out: list[list[float]] = []
    # Shared per-loop client (prism_sidecar._http) — embed_item runs once
    # per distilled item on the sync hot path, and each call used to pay
    # for a fresh connection pool + TLS handshake.
    client = _http.get_client()
    for i in range(0, len(texts), _BATCH):
        batch = texts[i : i + _BATCH]
        try:
            resp = await client.post(
                url,
                headers=headers,
                json={"model": EMBED_MODEL, "texts": batch, "type": kind},
                timeout=30.0,
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as e:
            raise EmbeddingError(f"MiniMax embeddings request failed: {e}") from e

        status = (body.get("base_resp") or {}).get("status_code")
        if status not in (0, None):
            msg = (body.get("base_resp") or {}).get("status_msg", "unknown")
            raise EmbeddingError(f"MiniMax embeddings error {status}: {msg}")
        vectors = body.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise EmbeddingError("MiniMax embeddings returned an unexpected shape")
        out.extend(vectors)
    return out


async def embed_query(text: str) -> list[float]:
    """Embed a single search query (uses the "query" projection)."""
    vecs = await embed_texts([text], kind="query")
    return vecs[0]


__all__ = [
    "EMBED_MODEL",
    "EMBED_DIM",
    "EmbedKind",
    "EmbeddingsUnavailable",
    "EmbeddingError",
    "embeddings_available",
    "embed_texts",
    "embed_query",
]
