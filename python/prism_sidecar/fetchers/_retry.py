"""Shared request-level retry + per-host throttle (v0.2c).

`retry_async(fn)` wraps a single HTTP request with bounded exponential
backoff. `HostThrottle` enforces a minimum interval between requests to
the same host so concurrent fetchers can't accidentally hammer B 站 /
YouTube. Both are pure-asyncio and take injectable `sleep` / `clock`
for fast, deterministic unit tests (no real sleeping in test runs).

Design doc: docs/design/retry-and-rate-limit.md §1 / §4.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, TypeVar
from urllib.parse import urlsplit

import httpx

from prism_sidecar.config import FETCH_MAX_RETRIES, FETCH_RETRY_BACKOFF_SEC

log = logging.getLogger(__name__)

T = TypeVar("T")

# A 429 with a huge Retry-After shouldn't stall the whole sync run.
RETRY_AFTER_CAP_SEC = 30.0


def default_retryable(exc: Exception) -> bool:
    """Transient errors → True; permanent errors → False.

    - timeouts / connection / transport errors: retry
    - HTTP 429 and 5xx: retry
    - other HTTP status errors (4xx): don't retry
    - anything else (parse errors, programming bugs): don't retry
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    """Honour a 429's Retry-After header (seconds form only), capped."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    if exc.response.status_code != 429:
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), RETRY_AFTER_CAP_SEC)
    except ValueError:
        # HTTP-date form — not worth parsing here; fall back to backoff.
        return None


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = FETCH_MAX_RETRIES,
    backoff_base: float = FETCH_RETRY_BACKOFF_SEC,
    retryable: Callable[[Exception], bool] = default_retryable,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
    describe: str = "",
) -> T:
    """Run `fn` with up to `max_retries` retries on transient errors.

    Backoff: `backoff_base * 2**(attempt-1)`, ±20% jitter, unless the
    server sent a usable Retry-After (which wins, capped at
    RETRY_AFTER_CAP_SEC). Non-retryable errors propagate immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 2):  # retries + the first try
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — filtered by `retryable`
            last_exc = exc
            if attempt > max_retries or not retryable(exc):
                raise
            backoff = _retry_after_seconds(exc)
            if backoff is None:
                # ±20% jitter so parallel fetchers don't retry in lockstep.
                backoff = backoff_base * (2 ** (attempt - 1)) * (0.8 + 0.4 * jitter())
            log.warning(
                "[retry] %s failed (attempt %d/%d): %s — retry in %.1fs",
                describe or "request", attempt, max_retries + 1, exc, backoff,
            )
            await sleep(backoff)
    # Unreachable: the loop either returned or raised.
    assert last_exc is not None
    raise last_exc


# ---- Per-host throttle ----------------------------------------------------

# Minimum seconds between two requests to the same host (suffix match).
# B 站 un-authed throttle is ~1 req/s; YouTube is stricter for anonymous
# clients. Everything else (RSS feeds: usually one request per sync)
# gets a token 0.2s interval that's effectively free.
_HOST_INTERVALS: list[tuple[str, float]] = [
    ("bilibili.com", 1.0),
    ("hdslb.com", 1.0),
    ("youtube.com", 1.5),
    ("googlevideo.com", 1.5),
    # arXiv API terms ask for ~1 request / 3s.
    ("arxiv.org", 3.0),
    # X/Twitter bridges (Nitter / self-hosted RSSHub) — be polite; public
    # instances rate-limit hard. The default bridge host is user-config,
    # so these only bite direct twitter.com / nitter.net hits.
    ("twitter.com", 1.5),
    ("x.com", 1.5),
    ("nitter.net", 2.0),
]
DEFAULT_MIN_INTERVAL_SEC = 0.2


class HostThrottle:
    """Process-wide minimum-interval limiter, keyed by host suffix.

    `await throttle.wait(url)` returns once it's polite to hit the URL's
    host again. State is per-instance; the module-level `throttle`
    singleton is what production code should use so limits hold across
    every fetcher in the process.
    """

    def __init__(
        self,
        *,
        intervals: list[tuple[str, float]] | None = None,
        default_interval: float = DEFAULT_MIN_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._intervals = list(intervals) if intervals is not None else list(_HOST_INTERVALS)
        self._default = default_interval
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _interval_for(self, host: str) -> float:
        for suffix, interval in self._intervals:
            if host == suffix or host.endswith("." + suffix):
                return interval
        return self._default

    async def wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return
        interval = self._interval_for(host)
        while True:
            async with self._lock:
                now = self._clock()
                last = self._last_request.get(host)
                if last is None or now - last >= interval:
                    self._last_request[host] = now
                    return
                delay = interval - (now - last)
            # Sleep outside the lock so other hosts aren't blocked.
            await self._sleep(delay)


throttle = HostThrottle()


def reset_throttle_for_tests(**kwargs: Any) -> HostThrottle:
    """Replace the singleton (tests only — production never calls this)."""
    global throttle
    throttle = HostThrottle(**kwargs)
    return throttle


__all__ = [
    "retry_async",
    "default_retryable",
    "HostThrottle",
    "throttle",
    "DEFAULT_MIN_INTERVAL_SEC",
    "RETRY_AFTER_CAP_SEC",
]
