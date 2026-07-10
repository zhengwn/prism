"""Offline tests for the Bilibili fetcher.

We deliberately do NOT hit B 站 — all `bilibili_api.user.User` /
`bilibili_api.video.Video` calls go through a fake module we inject
via monkeypatch into `prism_sidecar.fetchers.bilibili`. The subtitle
JSON download uses `respx` against httpx.

These cover the PoC acceptance criteria from the task brief:

  * mid 模式:UP 主投稿列表 → 字幕 → RawItem 列表
  * bvid 模式:单视频 → 字幕 → 单个 RawItem
  * 字幕下载失败 graceful 降级 (只剩标题 + 简介)
  * CC 字幕优先 + AI 字幕 fallback
  * 视频已存在 (按 url 去重) 跳过  — 由 sync 层负责,这里只验证
    RawItem.url 唯一
  * + 一些额外保险:空 config / 缺 cid / AI 字幕去重
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from httpx import Response

from prism_sidecar.fetchers import bilibili as bili_mod
from prism_sidecar.fetchers.base import FetchError
from prism_sidecar.fetchers.bilibili import BilibiliFetcher
from prism_sidecar.models import Source, SourceKind


# ----- helpers ----------------------------------------------------------


def _make_source(
    *,
    mid: str | None = None,
    bvid: str | None = None,
    keyword: str | None = None,
    source_id: str = "src_bili_test",
    name: str = "Test Bili Source",
) -> Source:
    cfg: dict[str, Any] = {}
    if mid:
        cfg["mid"] = mid
    if bvid:
        cfg["bvid"] = bvid
    if keyword:
        cfg["keyword"] = keyword
    return Source(
        id=source_id,
        name=name,
        kind=SourceKind.bilibili,
        url=f"https://space.bilibili.com/{mid}" if mid else "https://www.bilibili.com/video/placeholder",
        enabled=True,
        config_json=cfg,
    )


class _FakeVideo:
    """Fake `bilibili_api.video.Video` — one instance per bvid."""

    def __init__(self, bvid: str | None = None, aid: int | None = None):
        self.bvid = bvid
        self.aid = aid

    async def get_info(self) -> dict[str, Any]:
        # Tests poke _FakeVideo.last_get_info_bvid to verify the right
        # bvid was passed.
        _FakeVideo.last_get_info_bvid = self.bvid
        return _FakeVideo._info_by_bvid.get(self.bvid or "", {})

    async def get_subtitle(self, cid: int | None = None) -> dict[str, Any] | None:
        _FakeVideo.last_get_subtitle_cid = cid
        return _FakeVideo._subtitle_by_bvid.get(self.bvid or "")


class _FakeUser:
    """Fake `bilibili_api.user.User`."""

    def __init__(self, uid: int):
        self.uid = uid

    async def get_videos(self, pn: int = 1, ps: int = 30) -> dict[str, Any]:
        _FakeUser.last_query = (self.uid, pn, ps)
        return _FakeUser._vlist_by_mid.get(str(self.uid), {"data": {}})


def _install_fake_bilibili(monkeypatch) -> None:
    """Inject the fake bilibili_api.user / .video modules."""
    monkeypatch.setattr(bili_mod, "_bili_user", SimpleNamespace(User=_FakeUser))
    monkeypatch.setattr(bili_mod, "_bili_video", SimpleNamespace(Video=_FakeVideo))
    # Reset fake tables between tests.
    _FakeVideo._info_by_bvid = {}
    _FakeVideo._subtitle_by_bvid = {}
    _FakeVideo.last_get_info_bvid = None
    _FakeVideo.last_get_subtitle_cid = None
    _FakeUser._vlist_by_mid = {}
    _FakeUser.last_query = None


# ----- tests -------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_subtitle_track_prefers_human_zh(monkeypatch):
    """CC 字幕优先级高于 AI,且中文 CC 高于其他 CC。"""
    _install_fake_bilibili(monkeypatch)

    tracks = [
        {"lan": "en-US", "type": 1, "ai_type": 0, "subtitle_url": "https://x/a"},
        {"lan": "ai-zh", "type": 0, "ai_type": 1, "subtitle_url": "https://x/b"},
        {"lan": "zh-CN", "type": 1, "ai_type": 0, "subtitle_url": "https://x/c"},
    ]
    picked = bili_mod._pick_subtitle_track(tracks)
    assert picked is not None
    assert picked["lan"] == "zh-CN"
    assert picked["subtitle_url"] == "https://x/c"


@pytest.mark.asyncio
async def test_pick_subtitle_track_falls_back_to_ai(monkeypatch):
    """没有 CC 时,选 AI 字幕。"""
    _install_fake_bilibili(monkeypatch)

    tracks = [
        {"lan": "ai-zh", "type": 0, "ai_type": 1, "subtitle_url": "https://x/a"},
    ]
    picked = bili_mod._pick_subtitle_track(tracks)
    assert picked is not None
    assert bili_mod._pick_subtitle_kind(picked) == "ai"
    assert bili_mod._classify_subtitle_source(tracks, picked) == "ai_only"


@pytest.mark.asyncio
async def test_pick_subtitle_track_empty_returns_none(monkeypatch):
    _install_fake_bilibili(monkeypatch)
    assert bili_mod._pick_subtitle_track([]) is None
    assert bili_mod._classify_subtitle_source([], None) == "none"


@pytest.mark.asyncio
async def test_subtitle_body_to_markdown_dedups_repeats():
    body = [
        {"from": 0, "to": 2, "content": "你好"},
        {"from": 2, "to": 4, "content": "你好"},  # ghost repeat
        {"from": 4, "to": 6, "content": "世界"},
    ]
    md = bili_mod._subtitle_body_to_markdown(body, cue_kind="cc")
    assert "- [00:00] [CC] 你好" in md
    assert "- [00:04] [CC] 世界" in md
    # The ghost repeat must be gone.
    assert md.count("你好") == 1


@pytest.mark.asyncio
async def test_subtitle_body_to_markdown_emits_ai_tag_for_ai_track():
    """When the picked track is AI, every cue gets [AI] prefix.

    The bilibili-distiller parses these per-cue tags to split CC vs AI
    tracks downstream — so this is the cross-module contract test.
    """
    body = [
        {"from": 0, "to": 2, "content": "AI 字幕"},
    ]
    md = bili_mod._subtitle_body_to_markdown(body, cue_kind="ai")
    assert md == "- [00:00] [AI] AI 字幕"


@pytest.mark.asyncio
async def test_subtitle_body_to_markdown_omits_tag_when_unknown():
    """``cue_kind=unknown`` → no prefix; we don't want to mislead the LLM."""
    body = [
        {"from": 0, "to": 2, "content": "no provenance"},
    ]
    md = bili_mod._subtitle_body_to_markdown(body, cue_kind="unknown")
    assert md == "- [00:00] no provenance"


def test_format_timestamp_handles_long_videos():
    assert bili_mod._format_timestamp(0) == "00:00"
    assert bili_mod._format_timestamp(59) == "00:59"
    assert bili_mod._format_timestamp(60) == "01:00"
    assert bili_mod._format_timestamp(3661) == "01:01:01"


@pytest.mark.asyncio
async def test_bvid_mode_returns_single_rawitem(monkeypatch):
    """单 bvid: 一个 RawItem,字幕完整,url 是标准 BV 链接。"""
    _install_fake_bilibili(monkeypatch)

    bvid = "BV1xxxxxxxxx"
    _FakeVideo._info_by_bvid[bvid] = {
        "title": "GPT-5 解析",
        "desc": "全面评测 GPT-5。",
        "pubdate": 1700000000,
        "duration": 600,
        "owner": {"mid": 999, "name": "机器之心"},
        "pages": [{"cid": 12345}],
    }
    _FakeVideo._subtitle_by_bvid[bvid] = {
        "subtitles": [
            {
                "lan": "zh-CN",
                "lan_doc": "中文（中国大陆）",
                "type": 1,
                "ai_type": 0,
                "subtitle_url": "https://sub.test/sub.json",
            },
        ],
    }
    subtitle_body = {
        "body": [
            {"from": 0, "to": 2, "content": "大家好"},
            {"from": 2, "to": 4, "content": "今天聊 GPT-5"},
        ],
    }

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://sub.test/sub.json").mock(
            return_value=Response(200, json=subtitle_body)
        )
        items = await fetcher.fetch(
            _make_source(bvid=bvid),
        )

    assert len(items) == 1
    raw = items[0]
    assert raw.url == f"https://www.bilibili.com/video/{bvid}"
    assert raw.title == "GPT-5 解析"
    assert raw.author == "机器之心"
    assert raw.duration_sec == 600
    assert raw.content_type.value == "video"
    assert "字幕" in raw.content
    assert "GPT-5" in raw.content
    # Subtitle cues present in the markdown (with [CC] per-cue tag).
    assert "- [00:00] [CC] 大家好" in raw.content
    assert "- [00:02] [CC] 今天聊 GPT-5" in raw.content
    # Metadata audit fields populated.
    md = raw.metadata
    assert md["bvid"] == bvid
    assert md["subtitle_source"] == "cc_only"
    assert md["subtitle_kind"] == "cc"
    assert md["subtitle_track_count"] == 1
    # Pubdate round-trips.
    assert raw.published_at.tzinfo is not None
    assert raw.published_at.year >= 2023


@pytest.mark.asyncio
async def test_mid_mode_lists_up_videos(monkeypatch):
    """UP 主 mid: 拉 N 个视频,每个都 → RawItem,字幕都吃到。"""
    _install_fake_bilibili(monkeypatch)

    mid = "2025991408"
    _FakeUser._vlist_by_mid[mid] = {
        "data": {
            "list": {
                "vlist": [
                    {"bvid": "BVaaa1", "title": "video one", "author": "机器之心", "created": 1700000000, "length": "5:23"},
                    {"bvid": "BVaaa2", "title": "video two", "author": "机器之心", "created": 1700010000, "length": "12:00"},
                    {"bvid": "BVaaa3", "title": "video three", "author": "机器之心", "created": 1700020000, "length": "30:00"},
                ],
            },
        },
    }
    # Add minimal info + subtitle for each bvid.
    for bvid, cid in [("BVaaa1", 101), ("BVaaa2", 102), ("BVaaa3", 103)]:
        _FakeVideo._info_by_bvid[bvid] = {
            "title": f"info-{bvid}",
            "desc": "",
            "pubdate": 1700000000,
            "duration": 300,
            "owner": {"mid": int(mid), "name": "机器之心"},
            "pages": [{"cid": cid}],
        }
        _FakeVideo._subtitle_by_bvid[bvid] = {
            "subtitles": [
                {
                    "lan": "zh-CN",
                    "type": 1,
                    "ai_type": 0,
                    "subtitle_url": f"https://sub.test/{bvid}.json",
                },
            ],
        }

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    with respx.mock() as mock:
        for bvid in ("BVaaa1", "BVaaa2", "BVaaa3"):
            mock.get(f"https://sub.test/{bvid}.json").mock(
                return_value=Response(
                    200,
                    json={"body": [{"from": 0, "to": 1, "content": f"line {bvid}"}]},
                )
            )
        items = await fetcher.fetch(_make_source(mid=mid))

    assert len(items) == 3
    bvids = sorted(item.metadata["bvid"] for item in items)
    assert bvids == ["BVaaa1", "BVaaa2", "BVaaa3"]
    # Each item has a unique url (de-dup is the pipeline's job, but
    # the fetcher must not collapse them).
    urls = [item.url for item in items]
    assert len(set(urls)) == 3
    # All items have subtitle_kind=cc (we mocked CC for every video).
    assert all(item.metadata["subtitle_kind"] == "cc" for item in items)
    # The UP main() got called with the right mid / pn / ps.
    assert _FakeUser.last_query == (int(mid), 1, 20)


@pytest.mark.asyncio
async def test_subtitle_download_failure_graceful_fallback(monkeypatch):
    """字幕 JSON 下载 502 时,RawItem 仍有 title + 简介,标记 none。"""
    _install_fake_bilibili(monkeypatch)

    bvid = "BVfallback"
    _FakeVideo._info_by_bvid[bvid] = {
        "title": "fallback test",
        "desc": "this is the description",
        "pubdate": 1700000000,
        "duration": 100,
        "owner": {"mid": 1, "name": "机器之心"},
        "pages": [{"cid": 999}],
    }
    _FakeVideo._subtitle_by_bvid[bvid] = {
        "subtitles": [
            {
                "lan": "zh-CN",
                "type": 1,
                "ai_type": 0,
                "subtitle_url": "https://sub.test/broken.json",
            },
        ],
    }

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    with respx.mock() as mock:
        # 502 on the subtitle download — simulate B 站 hiccup.
        mock.get("https://sub.test/broken.json").mock(
            return_value=Response(502, text="upstream broken")
        )
        items = await fetcher.fetch(_make_source(bvid=bvid))

    assert len(items) == 1
    raw = items[0]
    assert raw.title == "fallback test"
    assert raw.content_type.value == "video"
    assert "this is the description" in raw.content
    # We tracked the track existed, but the body couldn't be fetched.
    assert raw.metadata["subtitle_track_count"] == 1
    assert raw.metadata["subtitle_kind"] == "cc"
    assert raw.metadata["subtitle_source"] == "cc_only"
    # No cue lines in the markdown (sub went "none" effectively).
    assert "无可用字幕" in raw.content


@pytest.mark.asyncio
async def test_cc_plus_ai_picks_cc(monkeypatch):
    """CC + AI 同时存在时,选 CC,并在 metadata 标 cc+ai 可用。"""
    _install_fake_bilibili(monkeypatch)

    bvid = "BVmix"
    _FakeVideo._info_by_bvid[bvid] = {
        "title": "mixed subtitles",
        "desc": "",
        "pubdate": 1700000000,
        "duration": 200,
        "owner": {"mid": 1, "name": "机器之心"},
        "pages": [{"cid": 555}],
    }
    _FakeVideo._subtitle_by_bvid[bvid] = {
        "subtitles": [
            {
                "lan": "zh-CN",
                "type": 1,
                "ai_type": 0,
                "subtitle_url": "https://sub.test/cc.json",
            },
            {
                "lan": "ai-zh",
                "type": 0,
                "ai_type": 1,
                "subtitle_url": "https://sub.test/ai.json",
            },
        ],
    }

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    # Don't use assert_all_called here — the AI route is intentionally
    # never hit (CC was picked), so respx would falsely flag it.
    with respx.mock(assert_all_called=False) as mock:
        # CC route hit, AI route never hit.
        mock.get("https://sub.test/cc.json").mock(
            return_value=Response(
                200, json={"body": [{"from": 0, "to": 1, "content": "CC line"}]},
            )
        )
        ai_route = mock.get("https://sub.test/ai.json").mock(
            return_value=Response(200, json={"body": []}),
        )
        items = await fetcher.fetch(_make_source(bvid=bvid))

    assert len(items) == 1
    raw = items[0]
    # Source has BOTH, picked CC.
    assert raw.metadata["subtitle_source"] == "cc+ai"
    assert raw.metadata["subtitle_kind"] == "cc"
    assert "- [00:00] [CC] CC line" in raw.content
    # Make sure AI route was NOT hit.
    assert ai_route.call_count == 0


@pytest.mark.asyncio
async def test_ai_only_when_no_cc(monkeypatch):
    """只有 AI 字幕时,选 AI 并标 ai_only。"""
    _install_fake_bilibili(monkeypatch)

    bvid = "BVai"
    _FakeVideo._info_by_bvid[bvid] = {
        "title": "ai sub",
        "desc": "",
        "pubdate": 1700000000,
        "duration": 200,
        "owner": {"mid": 1, "name": "PaperWeekly"},
        "pages": [{"cid": 777}],
    }
    _FakeVideo._subtitle_by_bvid[bvid] = {
        "subtitles": [
            {
                "lan": "ai-zh",
                "type": 0,
                "ai_type": 1,
                "subtitle_url": "https://sub.test/ai.json",
            },
        ],
    }

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    with respx.mock() as mock:
        mock.get("https://sub.test/ai.json").mock(
            return_value=Response(
                200, json={"body": [{"from": 0, "to": 2, "content": "AI 自摸"}]},
            )
        )
        items = await fetcher.fetch(_make_source(bvid=bvid))

    assert len(items) == 1
    raw = items[0]
    assert raw.metadata["subtitle_source"] == "ai_only"
    assert raw.metadata["subtitle_kind"] == "ai"
    assert "- [00:00] [AI] AI 自摸" in raw.content


@pytest.mark.asyncio
async def test_no_subtitle_returns_title_only(monkeypatch):
    """无字幕轨道时,RawItem 仍生成,只缺字幕段。"""
    _install_fake_bilibili(monkeypatch)

    bvid = "BVnosub"
    _FakeVideo._info_by_bvid[bvid] = {
        "title": "no subtitle here",
        "desc": "just a description",
        "pubdate": 1700000000,
        "duration": 100,
        "owner": {"mid": 1, "name": "智东西"},
        "pages": [{"cid": 111}],
    }
    _FakeVideo._subtitle_by_bvid[bvid] = {"subtitles": []}  # empty list

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    items = await fetcher.fetch(_make_source(bvid=bvid))

    assert len(items) == 1
    raw = items[0]
    assert raw.title == "no subtitle here"
    assert raw.metadata["subtitle_source"] == "none"
    assert raw.metadata["subtitle_track_count"] == 0
    # Description made it in even with no subtitles.
    assert "just a description" in raw.content
    # Markdown explicitly says no subtitle.
    assert "无可用字幕" in raw.content


@pytest.mark.asyncio
async def test_missing_config_raises_non_retryable(monkeypatch):
    """没有 mid / bvid / keyword → FetchError(retryable=False)（v0.2c 契约:
    配置错误属于整源失败,且重试也修不好）。"""
    _install_fake_bilibili(monkeypatch)

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    with pytest.raises(FetchError) as exc_info:
        await fetcher.fetch(
            Source(
                id="src_empty",
                name="empty",
                kind=SourceKind.bilibili,
                url="https://example.com",
                enabled=True,
                config_json={},
            ),
        )
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_keyword_mode_raises_non_retryable(monkeypatch):
    """keyword 是 v0.2c TODO——现在会作为整源错误浮出（用户能在
    sources.last_error 看到"未实现"而不是永远的静默空结果）。"""
    _install_fake_bilibili(monkeypatch)

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    with pytest.raises(FetchError) as exc_info:
        await fetcher.fetch(_make_source(keyword="AI 论文"))
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_get_info_failure_falls_back_to_vlist_entry(monkeypatch):
    """get_info 抛异常时,用 get_videos 的轻量 entry 兜底 (title/author/pubdate/duration)。"""
    _install_fake_bilibili(monkeypatch)

    # Override Video to raise on get_info for one specific bvid.
    real_video_cls = bili_mod._bili_video.Video

    class _BoomVideo(real_video_cls):  # type: ignore[misc]
        async def get_info(self) -> dict[str, Any]:  # type: ignore[override]
            raise RuntimeError("rate limited")

        async def get_subtitle(self, cid=None):  # type: ignore[override]
            # Pretend cid is unknown — subtitle will be skipped.
            return {"subtitles": []}

    monkeypatch.setattr(bili_mod._bili_video, "Video", _BoomVideo)

    mid = "339137722"
    _FakeUser._vlist_by_mid[mid] = {
        "data": {
            "list": {
                "vlist": [
                    {"bvid": "BVfallback", "title": "fallback entry", "author": "智东西",
                     "created": 1700000000, "length": "5:00"},
                ],
            },
        },
    }

    fetcher = BilibiliFetcher(inter_video_sleep=0)
    items = await fetcher.fetch(_make_source(mid=mid))

    assert len(items) == 1
    raw = items[0]
    assert raw.title == "fallback entry"
    assert raw.author == "智东西"
    assert raw.metadata["subtitle_source"] == "none"


@pytest.mark.asyncio
async def test_registry_returns_bilibili_fetcher(monkeypatch):
    """registry 在 kind=bilibili 时返回 BilibiliFetcher 实例。"""
    _install_fake_bilibili(monkeypatch)
    from prism_sidecar.fetchers.registry import get_fetcher

    fetcher = get_fetcher(_make_source(mid="123"))
    assert isinstance(fetcher, BilibiliFetcher)


@pytest.mark.asyncio
async def test_relative_subtitle_url_is_promoted():
    """有些 track 的 subtitle_url 是 ``//host/path`` (protocol-relative) —
    必须补成 ``https://`` 才能被 httpx 抓。
    """
    picked = {"subtitle_url": "//aisubtitle.hdslb.com/bfs/x.json"}
    assert bili_mod._subtitle_url(picked) == "https://aisubtitle.hdslb.com/bfs/x.json"

    picked2 = {"subtitle_url": "/bfs/y.json"}
    # Falls back to the B 站 aisubtitle CDN since that's the most common case.
    assert bili_mod._subtitle_url(picked2) == "https://aisubtitle.hdslb.com/bfs/y.json"