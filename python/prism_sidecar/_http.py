"""Shared per-event-loop httpx.AsyncClient (v0.5.x).

The fetch pipeline used to build a fresh AsyncClient (fresh connection
pool + TLS handshake) for every download — once per feed for RSS, once
per VIDEO for the YouTube / Bilibili subtitle fetches, and once per
embeddings batch. A shared client reuses connections; call sites pass
per-request ``headers=`` / ``timeout=``, which is all they ever varied.

Why per-LOOP instead of one module global: httpx pools hold loop-bound
primitives, so a client created on one event loop cannot be reused on
another — and pytest gives every test its own loop. Keyed weakly by
the running loop: production (one loop for the process lifetime) gets
exactly one client; tests get one per loop and the WeakKeyDictionary
lets dead loops' entries be collected.

``aclose_current()`` closes the *current* loop's client — called from
the FastAPI lifespan shutdown and the pytest autouse fixture teardown.
"""

from __future__ import annotations

import asyncio
import weakref

import httpx

from prism_sidecar.config import FETCH_TIMEOUT_SEC

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def get_client() -> httpx.AsyncClient:
    """The shared AsyncClient for the running loop (created on first use)."""
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SEC,
            follow_redirects=True,
        )
        _clients[loop] = client
    return client


async def aclose_current() -> None:
    """Close the running loop's shared client (lifespan / test teardown)."""
    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()


__all__ = ["get_client", "aclose_current"]
