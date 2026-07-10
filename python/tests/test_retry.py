"""Unit tests for fetchers/_retry.py (retry_async + HostThrottle).

All tests inject fake `sleep` / `clock` — nothing here actually sleeps.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from prism_sidecar.fetchers._retry import (
    DEFAULT_MIN_INTERVAL_SEC,
    RETRY_AFTER_CAP_SEC,
    HostThrottle,
    default_retryable,
    retry_async,
)


def _status_error(code: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.com/feed")
    resp = httpx.Response(code, request=req, headers=headers or {})
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=resp)


class _Sleeper:
    """Records requested sleep durations, never sleeps."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ---- default_retryable ----------------------------------------------------


def test_retryable_matrix():
    assert default_retryable(_status_error(500)) is True
    assert default_retryable(_status_error(503)) is True
    assert default_retryable(_status_error(429)) is True
    assert default_retryable(_status_error(404)) is False
    assert default_retryable(_status_error(403)) is False
    assert default_retryable(httpx.ConnectTimeout("t")) is True
    assert default_retryable(httpx.ConnectError("dns")) is True
    assert default_retryable(ValueError("json parse")) is False


# ---- retry_async ----------------------------------------------------------


@pytest.mark.asyncio
async def test_succeeds_first_try():
    sleeper = _Sleeper()
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return "ok"

    assert await retry_async(fn, sleep=sleeper) == "ok"
    assert calls == 1
    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds():
    sleeper = _Sleeper()
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _status_error(500)
        return "ok"

    result = await retry_async(
        fn, max_retries=2, backoff_base=1.0, sleep=sleeper, jitter=lambda: 0.5,
    )
    assert result == "ok"
    assert calls == 3
    # backoff_base * 2**(n-1) * (0.8 + 0.4*0.5) = 1.0, 2.0
    assert sleeper.calls == [1.0, 2.0]


@pytest.mark.asyncio
async def test_exhausts_retries_and_raises():
    sleeper = _Sleeper()
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _status_error(500)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(fn, max_retries=2, sleep=sleeper)
    assert calls == 3  # first try + 2 retries
    assert len(sleeper.calls) == 2


@pytest.mark.asyncio
async def test_non_retryable_raises_immediately():
    sleeper = _Sleeper()
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _status_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(fn, max_retries=5, sleep=sleeper)
    assert calls == 1
    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_retry_after_header_wins_over_backoff():
    sleeper = _Sleeper()
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error(429, headers={"Retry-After": "7"})
        return "ok"

    await retry_async(fn, max_retries=1, backoff_base=100.0, sleep=sleeper)
    assert sleeper.calls == [7.0]


@pytest.mark.asyncio
async def test_retry_after_is_capped():
    sleeper = _Sleeper()
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error(429, headers={"Retry-After": "9999"})
        return "ok"

    await retry_async(fn, max_retries=1, sleep=sleeper)
    assert sleeper.calls == [RETRY_AFTER_CAP_SEC]


@pytest.mark.asyncio
async def test_jitter_bounds():
    """Backoff stays within ±20% of the nominal value."""
    for j in (0.0, 1.0):
        sleeper = _Sleeper()
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _status_error(500)
            return "ok"

        await retry_async(
            fn, max_retries=1, backoff_base=10.0, sleep=sleeper, jitter=lambda j=j: j,
        )
        assert len(sleeper.calls) == 1
        # j=0 → 8.0 (−20%), j=1 → 12.0 (+20%). Compare with a tolerance: in
        # binary float `10.0 * (0.8 + 0.4 * 1.0) == 12.000000000000002`, which
        # trips a strict `<= 12.0` upper bound.
        assert sleeper.calls[0] == pytest.approx(8.0 + 4.0 * j)
        calls = 0


# ---- HostThrottle ---------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_throttle_first_request_is_free():
    clock = _Clock()
    sleeper = _Sleeper()
    t = HostThrottle(clock=clock, sleep=sleeper)
    await t.wait("https://www.bilibili.com/video/BV1")
    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_throttle_enforces_interval_same_host():
    clock = _Clock()
    sleeps: list[float] = []

    async def sleep(sec: float) -> None:
        sleeps.append(sec)
        clock.now += sec  # advancing time lets the loop exit

    t = HostThrottle(clock=clock, sleep=sleep)
    await t.wait("https://api.bilibili.com/x/player")
    await t.wait("https://api.bilibili.com/x/web-interface")
    # bilibili.com interval is 1.0s and no time passed in between.
    assert sleeps and abs(sum(sleeps) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_throttle_suffix_matching_and_defaults():
    t = HostThrottle()
    assert t._interval_for("api.bilibili.com") == 1.0
    assert t._interval_for("bilibili.com") == 1.0
    assert t._interval_for("aisubtitle.hdslb.com") == 1.0
    assert t._interval_for("www.youtube.com") == 1.5
    assert t._interval_for("notbilibili.com") == DEFAULT_MIN_INTERVAL_SEC
    assert t._interval_for("simonwillison.net") == DEFAULT_MIN_INTERVAL_SEC


@pytest.mark.asyncio
async def test_throttle_different_hosts_do_not_block_each_other():
    clock = _Clock()
    sleeper = _Sleeper()
    t = HostThrottle(clock=clock, sleep=sleeper)
    await t.wait("https://www.bilibili.com/a")
    await t.wait("https://simonwillison.net/atom/everything/")
    # Different host — no wait even though bilibili was just hit.
    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_throttle_after_interval_passes_no_sleep():
    clock = _Clock()
    sleeper = _Sleeper()
    t = HostThrottle(clock=clock, sleep=sleeper)
    await t.wait("https://www.youtube.com/watch?v=x")
    clock.now += 2.0  # > 1.5s interval
    await t.wait("https://www.youtube.com/watch?v=y")
    assert sleeper.calls == []
