"""Offline tests for the YouTube fetcher.

Same philosophy as test_bilibili_fetcher.py: never hit YouTube. All
yt-dlp calls go through a fake module injected via monkeypatch into
`prism_sidecar.fetchers.youtube`; caption JSON downloads use respx.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import respx
from httpx import Response

from prism_sidecar.distillers.bilibili_prompt import (
    is_video_transcript,
    should_use_bilibili_prompt,
)
from prism_sidecar.fetchers import youtube as yt_mod
from prism_sidecar.fetchers.base import FetchError
from prism_sidecar.fetchers.youtube import (
    YouTubeFetcher,
    json3_events_to_body,
    parse_channel_ref,
    parse_video_ref,
    pick_caption_track,
)
from prism_sidecar.models import ContentType, Source, SourceKind

# ----- helpers ----------------------------------------------------------


def _make_source(
    *,
    channel: str | None = None,
    video: str | None = None,
    playlist: str | None = None,
    source_id: str = "src_yt_test",
) -> Source:
    cfg: dict[str, Any] = {}
    if channel:
        cfg["channel"] = channel
    if video:
        cfg["video"] = video
    if playlist:
        cfg["playlist"] = playlist
    return Source(
        id=source_id,
        name="Test YT Source",
        kind=SourceKind.youtube,
        url="https://www.youtube.com/@test",
        enabled=True,
        config_json=cfg,
    )


_NOW = datetime.now(timezone.utc)
# No query string on purpose — respx matches the exact URL, and the
# fetcher passes the track URL through verbatim anyway.
CAPTION_URL = "https://captions.test/abcdefghijk.json3"


def _video_info(
    video_id: str = "abcdefghijk",
    *,
    ts: datetime | None = None,
    subtitles: dict | None = None,
    automatic: dict | None = None,
) -> dict[str, Any]:
    when = ts or (_NOW - timedelta(days=1))
    return {
        "id": video_id,
        "title": f"Video {video_id}",
        "description": "A test video about LLM agents.",
        "uploader": "Test Channel",
        "channel_id": "UCtest0000000000000000",
        "timestamp": int(when.timestamp()),
        "upload_date": when.strftime("%Y%m%d"),
        "duration": 725,
        "subtitles": subtitles if subtitles is not None else {},
        "automatic_captions": automatic if automatic is not None else {},
    }


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL. Class attrs configure behaviour."""

    flat_response: dict[str, Any] = {}
    video_responses: dict[str, Any] = {}
    raise_on_flat: Exception | None = None

    def __init__(self, opts: dict[str, Any]) -> None:
        self._opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url: str, download: bool = False):
        if self._opts.get("extract_flat"):
            if type(self).raise_on_flat is not None:
                raise type(self).raise_on_flat
            return type(self).flat_response
        for vid, info in type(self).video_responses.items():
            if vid in url:
                if isinstance(info, Exception):
                    raise info
                return info
        raise RuntimeError(f"unexpected url {url}")


def _install_fake_ytdlp(monkeypatch, **attrs):
    fake_cls = type("FakeYoutubeDL", (_FakeYoutubeDL,), {
        "flat_response": {}, "video_responses": {}, "raise_on_flat": None,
        **attrs,
    })
    fake_mod = type("FakeYtDlpModule", (), {"YoutubeDL": fake_cls})
    monkeypatch.setattr(yt_mod, "_yt_dlp", fake_mod)
    return fake_cls


def _fetcher() -> YouTubeFetcher:
    return YouTubeFetcher(inter_video_sleep=0)


JSON3 = {
    "events": [
        {"tStartMs": 0, "segs": [{"utf8": "hello"}, {"utf8": " world"}]},
        {"tStartMs": 65_000, "segs": [{"utf8": "hello world"}]},   # dedupe
        {"tStartMs": 70_000, "segs": [{"utf8": "\n"}]},            # dropped
        {"tStartMs": 3_725_000, "segs": [{"utf8": "the end"}]},    # > 1h
    ]
}


# ----- pure helpers ------------------------------------------------------


def test_parse_video_ref():
    assert parse_video_ref("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_ref("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1") == "dQw4w9WgXcQ"
    assert parse_video_ref("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_ref("not a video") is None
    assert parse_video_ref("") is None


def test_parse_channel_ref():
    assert parse_channel_ref("@lexfridman") == "https://www.youtube.com/@lexfridman/videos"
    assert (
        parse_channel_ref("UC2D2CMWXMOVWx7giW1n3LIg")
        == "https://www.youtube.com/channel/UC2D2CMWXMOVWx7giW1n3LIg/videos"
    )
    assert (
        parse_channel_ref("https://www.youtube.com/@lexfridman")
        == "https://www.youtube.com/@lexfridman/videos"
    )
    assert (
        parse_channel_ref("https://www.youtube.com/@lexfridman/videos")
        == "https://www.youtube.com/@lexfridman/videos"
    )
    assert parse_channel_ref("garbage") is None


def test_pick_caption_track_tiers():
    zh_manual = {"zh-Hans": [{"ext": "json3", "url": "u-zh-cc"}]}
    en_manual = {"en": [{"ext": "json3", "url": "u-en-cc"}]}
    zh_auto = {"zh-Hans": [{"ext": "json3", "url": "u-zh-ai"}]}
    en_auto = {"en": [{"ext": "json3", "url": "u-en-ai"}]}

    # Tier 1: manual zh beats everything
    assert pick_caption_track({**zh_manual, **en_manual}, {**zh_auto, **en_auto}) == (
        "u-zh-cc", "zh-Hans", "cc",
    )
    # Tier 2: manual en when no manual zh
    assert pick_caption_track(en_manual, zh_auto) == ("u-en-cc", "en", "cc")
    # Tier 3: automatic zh when no manual at all
    assert pick_caption_track({}, {**zh_auto, **en_auto}) == ("u-zh-ai", "zh-Hans", "ai")
    # Tier 4: automatic en as last resort
    assert pick_caption_track({}, en_auto) == ("u-en-ai", "en", "ai")
    # No json3 format → unavailable
    assert pick_caption_track({"en": [{"ext": "vtt", "url": "u"}]}, {}) is None
    assert pick_caption_track(None, None) is None


def test_json3_events_to_body():
    body = json3_events_to_body(JSON3)
    assert body[0] == {"from": 0.0, "content": "hello world"}
    assert body[1]["from"] == 65.0
    # "\n"-only seg dropped; > 1h timestamp preserved as float seconds
    assert body[-1]["from"] == 3725.0
    assert json3_events_to_body({}) == []
    assert json3_events_to_body({"events": "bogus"}) == []


# ----- video mode ---------------------------------------------------------


@pytest.mark.asyncio
async def test_video_mode_builds_rawitem_with_cc_subtitle(monkeypatch):
    _install_fake_ytdlp(monkeypatch, video_responses={
        "abcdefghijk": _video_info(subtitles={
            "zh-Hans": [{"ext": "json3", "url": CAPTION_URL}],
        }),
    })
    with respx.mock() as mock:
        mock.get(CAPTION_URL).mock(
            return_value=Response(200, content=json.dumps(JSON3).encode())
        )
        items = await _fetcher().fetch(
            _make_source(video="abcdefghijk"), lookback_days=7,
        )

    assert len(items) == 1
    raw = items[0]
    assert raw.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert raw.content_type == ContentType.video
    assert raw.duration_sec == 725
    assert raw.metadata["feed_kind"] == "youtube"
    assert raw.metadata["subtitle_kind"] == "cc"
    # Shared cue format: the distiller keys off these prefixes.
    assert "- [00:00] [CC] hello world" in raw.content
    assert "- [01:02:05] [CC] the end" in raw.content
    assert "频道主: Test Channel" in raw.content


@pytest.mark.asyncio
async def test_video_mode_degrades_gracefully_without_captions(monkeypatch):
    _install_fake_ytdlp(monkeypatch, video_responses={
        "abcdefghijk": _video_info(),  # no caption tracks at all
    })
    items = await _fetcher().fetch(_make_source(video="abcdefghijk"))
    assert len(items) == 1
    assert "无可用字幕" in items[0].content
    assert items[0].metadata["subtitle_kind"] == "unknown"


@pytest.mark.asyncio
async def test_video_mode_survives_caption_download_failure(monkeypatch):
    _install_fake_ytdlp(monkeypatch, video_responses={
        "abcdefghijk": _video_info(automatic={
            "en": [{"ext": "json3", "url": CAPTION_URL}],
        }),
    })
    with respx.mock() as mock:
        mock.get(CAPTION_URL).mock(
            return_value=Response(404, text="gone")
        )
        items = await _fetcher().fetch(_make_source(video="abcdefghijk"))
    # Caption failure is per-item degradation, not a FetchError.
    assert len(items) == 1
    assert "无可用字幕" in items[0].content


# ----- channel mode -------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_mode_lists_and_builds(monkeypatch):
    recent = _NOW - timedelta(days=1)
    older = _NOW - timedelta(days=2)
    _install_fake_ytdlp(
        monkeypatch,
        flat_response={"entries": [
            {"id": "aaaaaaaaaaa", "title": "A"},
            {"id": "bbbbbbbbbbb", "title": "B"},
        ]},
        video_responses={
            "aaaaaaaaaaa": _video_info("aaaaaaaaaaa", ts=recent),
            "bbbbbbbbbbb": _video_info("bbbbbbbbbbb", ts=older),
        },
    )
    items = await _fetcher().fetch(
        _make_source(channel="@testchannel"), lookback_days=7,
    )
    assert [i.metadata["video_id"] for i in items] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


@pytest.mark.asyncio
async def test_channel_mode_lookback_cutoff_stops_iteration(monkeypatch):
    """lookback_days 真正生效:列表按时间倒序,首条过期视频处停止。"""
    _install_fake_ytdlp(
        monkeypatch,
        flat_response={"entries": [
            {"id": "aaaaaaaaaaa"},
            {"id": "bbbbbbbbbbb"},   # 40 days old — cutoff hits here
            {"id": "ccccccccccc"},   # must never be fetched
        ]},
        video_responses={
            "aaaaaaaaaaa": _video_info("aaaaaaaaaaa", ts=_NOW - timedelta(days=1)),
            "bbbbbbbbbbb": _video_info("bbbbbbbbbbb", ts=_NOW - timedelta(days=40)),
            # "ccccccccccc" intentionally absent: reaching it would raise.
        },
    )
    items = await _fetcher().fetch(
        _make_source(channel="@testchannel"), lookback_days=7,
    )
    assert [i.metadata["video_id"] for i in items] == ["aaaaaaaaaaa"]


@pytest.mark.asyncio
async def test_channel_mode_per_video_failure_skips(monkeypatch):
    _install_fake_ytdlp(
        monkeypatch,
        flat_response={"entries": [
            {"id": "aaaaaaaaaaa", "title": "A fallback title", "uploader": "F"},
            {"id": "bbbbbbbbbbb"},
        ]},
        video_responses={
            # extract_info fails for A → falls back to list entry.
            "aaaaaaaaaaa": RuntimeError("bot check"),
            "bbbbbbbbbbb": _video_info("bbbbbbbbbbb"),
        },
    )
    items = await _fetcher().fetch(
        _make_source(channel="@testchannel"), lookback_days=7,
    )
    # A degraded to fallback metadata; B fully built. Nothing raised.
    assert len(items) == 2
    assert items[0].title == "A fallback title"
    assert items[1].metadata["video_id"] == "bbbbbbbbbbb"


@pytest.mark.asyncio
async def test_channel_listing_failure_raises_fetch_error(monkeypatch):
    _install_fake_ytdlp(monkeypatch, raise_on_flat=RuntimeError("403"))
    with pytest.raises(FetchError):
        await _fetcher().fetch(_make_source(channel="@testchannel"))


# ----- contract cases -----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_ytdlp_raises_non_retryable(monkeypatch):
    monkeypatch.setattr(yt_mod, "_yt_dlp", None)
    with pytest.raises(FetchError) as exc_info:
        await _fetcher().fetch(_make_source(video="abcdefghijk"))
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_missing_config_raises_non_retryable(monkeypatch):
    _install_fake_ytdlp(monkeypatch)
    with pytest.raises(FetchError) as exc_info:
        await _fetcher().fetch(_make_source())
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_playlist_mode_raises_non_retryable(monkeypatch):
    _install_fake_ytdlp(monkeypatch)
    with pytest.raises(FetchError) as exc_info:
        await _fetcher().fetch(_make_source(playlist="PLxxxx"))
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_accepts_lookback_days_kwarg(monkeypatch):
    """Pipeline call-shape regression (the lookback_days lesson)."""
    _install_fake_ytdlp(monkeypatch, video_responses={
        "abcdefghijk": _video_info(),
    })
    items = await _fetcher().fetch(
        _make_source(video="abcdefghijk"), lookback_days=30,
    )
    assert len(items) == 1


# ----- prompt integration ---------------------------------------------------


@pytest.mark.asyncio
async def test_real_rawitem_hits_video_prompt_detection(monkeypatch):
    """真实 fetcher 产出的 RawItem 必须命中 prompt 检测函数——不允许只用
    手写 fixture(v0.2c 复查里 is_bilibili 检查错 metadata key 的教训)。"""
    _install_fake_ytdlp(monkeypatch, video_responses={
        "abcdefghijk": _video_info(),
    })
    items = await _fetcher().fetch(_make_source(video="abcdefghijk"))
    raw = items[0]
    assert is_video_transcript(raw) is True
    assert should_use_bilibili_prompt(raw) is True
    # And the preferred metadata path (not the URL fallback) is what fires.
    raw.url = "https://example.com/mirror"
    assert is_video_transcript(raw) is True
