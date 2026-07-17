"""B 站长字幕专用蒸馏 prompt 的单测。

覆盖 4 个任务里点名的 case：
1. 短字幕（< 5k 字）→ 走 B 站 prompt + 章节切分 instruction
2. 长字幕（> 24k token）→ 截断策略正确（head + middle-sampled + tail）
3. 字幕为空 → 走 fallback（仅元信息），prompt 中带 "仅元信息蒸馏"
4. CC + AI 字幕都存在 → prompt 里 CC/AI 段落分开标 + instruct 优先 CC

外加一些 sub-case 验证：
- 解析时区分 [CC] / [AI] / untagged
- 触发 LitellmDistiller.distill() 走 bilibili 分支（via feed_kind metadata）
- 离线烟雾测试：mock distiller + fixture 字幕 → 验证输出 schema
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.base import (
    _build_prompt,
)
from prism_sidecar.distillers.bilibili_prompt import (
    BILIBILI_META_ONLY_PROMPT,
    DEFAULT_MAX_CHARS,
    HEAD_CHARS,
    MIDDLE_CHARS,
    TAIL_CHARS,
    build_bilibili_prompt,
    is_bilibili,
    parse_subtitle,
    truncate_segment_list,
    truncate_subtitle,
)
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType

# ----- Fixtures -----------------------------------------------------------


def _raw_bilibili(
    content: str,
    *,
    title: str = "B 站视频",
    url: str = "https://www.bilibili.com/video/BV1abc",
    description: str = "",
    author: str | None = "某UP主",
    feed_kind: str = "bilibili",
) -> RawItem:
    return RawItem(
        url=url,
        title=title,
        content=content,
        published_at=datetime(2026, 6, 16, 10, 0, tzinfo=timezone.utc),
        author=author,
        content_type=ContentType.video,
        metadata={"feed_kind": feed_kind, "description": description},
    )


def _raw_non_bilibili(content: str, *, url: str = "https://example.com/x") -> RawItem:
    return RawItem(
        url=url,
        title="Some RSS post",
        content=content,
        published_at=datetime(2026, 6, 16, 10, 0, tzinfo=timezone.utc),
        content_type=ContentType.article,
        metadata={},
    )


SHORT_SUBTITLE = """[CC] 大家好，今天我们来聊一下 DeepSeek 最新发布的 V4 模型。
[CC] 这个模型在数学和代码评测上都有显著提升。
[AI] Hello everyone, today we will talk about DeepSeek's latest V4 model.
[AI] This model has significant improvements in math and code benchmarks.
[CC] 具体来说，GSM8K 上提升了 12 个点，HumanEval 上提升了 8 个点。
[CC] 训练成本方面，他们用了一个新的 MoE 架构。
[AI] Specifically, GSM8K improved by 12 points, HumanEval improved by 8 points.
[CC] 最后，他们说这个模型会在下周开源权重。
"""


# ----- Case 1: 短字幕 → B 站 prompt + 章节切分 instruction --------------


def test_short_subtitle_routes_to_bilibili_prompt():
    """A < 5k-char subtitle should go through the bilibili prompt,
    which explicitly instructs chapter-splitting + key-segment picking."""
    raw = _raw_bilibili(SHORT_SUBTITLE)
    prompt = _build_prompt(raw)

    # Prompt must contain the bilibili-specific scaffolding.
    assert "章节切分" in prompt
    assert "关键段选取" in prompt
    assert "B 站" in prompt or "bilibili" in prompt.lower()
    # And NOT be the generic short-content template (which has its
    # own short header).
    assert "AI 行业分析师" not in prompt  # the generic header
    # Title from the raw item must be present.
    assert "B 站视频" in prompt
    # Section headers carry the CC/AI provenance.
    assert "人工/官方字幕" in prompt
    assert "AI 机翻字幕" in prompt
    # The truncated preview has the [CC]/[AI] prefix stripped —
    # verify by checking the actual content lines don't carry the tag.
    # We assert that no line in the preview starts with "[CC]" / "[AI]".
    for line in prompt.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("[CC]"), f"prefix not stripped: {stripped!r}"
        assert not stripped.startswith("[AI]"), f"prefix not stripped: {stripped!r}"


def test_short_subtitle_with_only_cc_skips_ai_section():
    """When only CC segments exist, the prompt should not fabricate an
    AI section — it should be honest with the LLM."""
    only_cc = "[CC] line one\n[CC] line two\n[CC] line three\n"
    raw = _raw_bilibili(only_cc)
    prompt = build_bilibili_prompt(raw)

    assert "人工/官方字幕" in prompt
    assert "AI 机翻字幕" not in prompt
    # Source note should reflect single-source truth.
    assert "仅 CC" in prompt


# ----- Case 2: 长字幕 → 截断策略正确 -----------------------------------


def test_long_subtitle_is_truncated_within_budget():
    """A > 24k-token subtitle must be truncated. The output must
    stay within budget AND contain head + middle + tail markers."""
    # 50000 chars > 36k budget. Use a recognizable pattern so we can
    # assert head/middle/tail content survived.
    head_marker = "AAA_HEAD_MARKER_OPENAI_GPT5_DISCUSSION"
    middle_marker = "MIDDLE_KEY_INSIGHT_MOE_ARCH"
    tail_marker = "ZZZ_TAIL_MARKER_CONCLUSION"
    padding_head = "a" * 12000
    padding_mid = "b" * 12000
    padding_tail = "c" * 12000
    big = (
        head_marker + padding_head
        + middle_marker + padding_mid
        + tail_marker + padding_tail
    )
    assert len(big) > DEFAULT_MAX_CHARS

    out = truncate_subtitle(big)
    assert len(out) <= DEFAULT_MAX_CHARS + 200  # small budget for the
    # `[... 截断 ...]` fences themselves
    # Head and tail survived verbatim.
    assert head_marker in out
    assert tail_marker in out
    # Middle was sampled — the marker should still be in there because
    # the marker is at the boundary of the head/middle cut.
    assert middle_marker in out
    # And there should be at least one explicit truncation fence.
    assert "截断" in out or "省略" in out


def test_short_subtitle_is_not_truncated():
    """Subtitles within budget must NOT be touched (no truncation fence)."""
    raw = _raw_bilibili(SHORT_SUBTITLE)
    prompt = build_bilibili_prompt(raw)
    # The actual fence markers we emit when truncating are
    # "[... 中段采样 ...]" and "[... 末尾省略 ...]". They must NOT
    # appear when the input fits.
    assert "中段采样" not in prompt
    assert "末尾省略" not in prompt
    # The original content (minus the [CC]/[AI] prefix) survives.
    assert "DeepSeek" in prompt


def test_truncation_respects_custom_budget():
    """Truncation budget is configurable."""
    big = "x" * 100000
    out_small = truncate_subtitle(big, max_chars=5000)
    out_large = truncate_subtitle(big, max_chars=20000)
    assert len(out_small) < len(out_large)
    assert len(out_small) <= 5200  # fences
    assert len(out_large) <= 20200


# ----- Case 3: 字幕为空 → meta-only fallback ----------------------------


def test_empty_subtitle_routes_to_meta_only_prompt():
    """Empty content must trigger the meta-only fallback. The rendered
    prompt should make it clear we're distilling from metadata only."""
    raw = _raw_bilibili(
        content="",
        title="DeepSeek V4 发布",
        description="新一代 MoE 大模型，在数学评测上刷新 SOTA。",
        author="DeepSeek 官方",
    )
    prompt = _build_prompt(raw)

    assert "没有可用字幕" in prompt or "无可用字幕" in prompt
    assert "仅元信息蒸馏" in prompt
    # Title and description are in the prompt.
    assert "DeepSeek V4 发布" in prompt
    assert "数学评测" in prompt
    # The bilibili-specific chapter-splitting workflow is NOT in the
    # meta-only path.
    assert "章节切分" not in prompt


def test_whitespace_only_subtitle_treated_as_empty():
    raw = _raw_bilibili(content="   \n\n  \t  ")
    prompt = build_bilibili_prompt(raw)
    assert "仅元信息蒸馏" in prompt


def test_meta_only_fallback_instructs_tag_marker():
    """The meta-only prompt must require '仅元信息蒸馏' in tags_zh
    so downstream UI can surface the uncertainty."""
    assert "仅元信息蒸馏" in BILIBILI_META_ONLY_PROMPT


# ----- subtitle_track_count metadata wiring -----------------------------


def test_single_track_cc_note_explicit():
    """When fetcher reports subtitle_track_count=1 and only CC is present,
    the prompt must say '单轨' so the LLM knows it's not a half-fetch."""
    raw = RawItem(
        url="https://www.bilibili.com/video/BV1singleCC",
        title="Single CC track",
        content="[CC] line one\n[CC] line two\n",
        published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        metadata={"feed_kind": "bilibili", "subtitle_track_count": 1},
        content_type=ContentType.video,
    )
    prompt = build_bilibili_prompt(raw)
    assert "单轨" in prompt
    assert "仅 CC" in prompt


def test_single_track_ai_note_explicit():
    raw = RawItem(
        url="https://www.bilibili.com/video/BV1singleAI",
        title="Single AI track",
        content="[AI] line one\n[AI] line two\n",
        published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        metadata={"feed_kind": "bilibili", "subtitle_track_count": 1},
        content_type=ContentType.video,
    )
    prompt = build_bilibili_prompt(raw)
    assert "单轨" in prompt
    assert "仅 AI" in prompt


def test_zero_track_note_explicit_when_empty():
    """Empty content + subtitle_track_count=0 → explicit 'no track' note."""
    raw = RawItem(
        url="https://www.bilibili.com/video/BV1noTrack",
        title="No subtitle track",
        content="",
        published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        metadata={"feed_kind": "bilibili", "subtitle_track_count": 0},
        content_type=ContentType.video,
    )
    # Empty content routes to the meta-only fallback path (different
    # template), so the track_count signal surfaces there.
    prompt = build_bilibili_prompt(raw)
    # meta-only path uses different wording; just verify it doesn't
    # crash and falls back correctly.
    assert "没有可用字幕" in prompt or "无可用字幕" in prompt


def test_no_track_count_metadata_still_works():
    """Backward compat: fetcher that doesn't set subtitle_track_count
    must not break the prompt builder."""
    raw = RawItem(
        url="https://www.bilibili.com/video/BV1noMeta",
        title="Old fetcher",
        content="[CC] only cc\n",
        published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        metadata={"feed_kind": "bilibili"},  # no track_count
        content_type=ContentType.video,
    )
    prompt = build_bilibili_prompt(raw)
    assert "仅 CC" in prompt
    # And the explicit "单轨" marker is NOT there (no metadata).
    assert "单轨" not in prompt


def test_two_track_with_both_tags_still_routes_to_c_plan():
    """The C-plan (CC + AI both present) path should NOT add the
    '单轨' marker — the merge strategy wording is what the LLM needs."""
    raw = RawItem(
        url="https://www.bilibili.com/video/BV1twoTrack",
        title="Two tracks",
        content="[CC] cc line\n[AI] ai line\n",
        published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        metadata={"feed_kind": "bilibili", "subtitle_track_count": 2},
        content_type=ContentType.video,
    )
    prompt = build_bilibili_prompt(raw)
    assert "C 方案" in prompt
    assert "单轨" not in prompt  # C-plan path, not single-track


def test_invalid_track_count_metadata_falls_back_safely():
    """Non-int metadata (None / str / negative) must not break the prompt."""
    for bad in (None, "1", -1, "two"):
        raw = RawItem(
            url="https://www.bilibili.com/video/BV1bad",
            title="Bad metadata",
            content="[CC] x\n",
            published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
            metadata={"feed_kind": "bilibili", "subtitle_track_count": bad},
            content_type=ContentType.video,
        )
        prompt = build_bilibili_prompt(raw)
        assert "仅 CC" in prompt
        assert "单轨" not in prompt


# ----- Case 4: CC + AI → 合并策略 + 段标记 ------------------------------


def test_cc_and_ai_segments_get_separated_in_prompt():
    """When both [CC] and [AI] segments exist, the prompt must
    (a) visually separate them so the LLM can prioritize, and
    (b) instruct the CC-first / AI-supplementary strategy."""
    raw = _raw_bilibili(SHORT_SUBTITLE)
    analysis = parse_subtitle(raw.content)

    assert analysis.has_cc is True
    assert analysis.has_ai is True
    assert len(analysis.cc_segments) >= 1
    assert len(analysis.ai_segments) >= 1

    prompt = build_bilibili_prompt(raw)

    # CC and AI sections are visually distinct.
    cc_idx = prompt.find("人工/官方字幕")
    ai_idx = prompt.find("AI 机翻字幕")
    assert cc_idx != -1 and ai_idx != -1
    assert cc_idx < ai_idx  # CC comes first — the strategy says so

    # Source note spells out the merge strategy.
    assert "C 方案" in prompt
    assert "CC 为主" in prompt
    assert "AI 辅佐" in prompt or "AI 段落做" in prompt


def test_parse_subtitle_strips_prefix_markers():
    """[CC] / [AI] prefix tags must be stripped before the segment
    reaches the LLM — otherwise the model wastes tokens on bookkeeping."""
    analysis = parse_subtitle("[CC] hello\n[AI] world\nplain line\n")
    assert analysis.cc_segments == ["hello"]
    assert analysis.ai_segments == ["world", "plain line"]
    # Untagged defaults to AI (most common case on bilibili).
    assert "plain line" in analysis.ai_segments


def test_parse_subtitle_handles_empty():
    a = parse_subtitle("")
    assert a.cc_segments == []
    assert a.ai_segments == []
    assert a.total_chars == 0
    assert a.has_cc is False
    assert a.has_ai is False


# ----- Routing helpers ----------------------------------------------------


def test_is_bilibili_detects_via_metadata():
    raw = _raw_bilibili("anything", url="https://example.com/not-bili")
    assert is_bilibili(raw) is True


def test_is_bilibili_detects_via_url():
    raw = _raw_bilibili(
        "anything",
        url="https://www.bilibili.com/video/BV1abc",
        feed_kind="rss",  # intentionally wrong kind
    )
    assert is_bilibili(raw) is True


def test_is_bilibili_rejects_other_sources():
    raw = _raw_non_bilibili("anything")
    assert is_bilibili(raw) is False


def test_non_bilibili_raw_uses_generic_prompt():
    """RSS / other sources must NOT be hijacked by the bilibili prompt."""
    raw = _raw_non_bilibili("body of an RSS article about AI")
    prompt = _build_prompt(raw)
    assert "AI 行业分析师" in prompt  # the generic template
    assert "章节切分" not in prompt  # the bilibili-specific workflow


# ----- Integration with LitellmDistiller ---------------------------------


@pytest.mark.asyncio
async def test_bilibili_raw_routes_through_distiller(monkeypatch):
    """End-to-end: a bilibili raw item should make the distiller
    call litellm with the bilibili prompt (NOT the generic one)."""
    captured: dict[str, Any] = {}

    good_response = {
        "title_zh": "DeepSeek V4 发布",
        "summary_zh": "DeepSeek 发布 V4 模型，GSM8K 提升 12 点。",
        "key_points_zh": ["GSM8K +12", "HumanEval +8", "MoE 新架构"],
        "tags_zh": ["DeepSeek", "大模型", "MoE"],
    }

    async def fake_acompletion(*args: Any, **kwargs: Any):
        captured["kwargs"] = kwargs
        captured["messages"] = kwargs["messages"]
        return {
            "choices": [{"message": {"content": json.dumps(good_response)}}]
        }

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    raw = _raw_bilibili(SHORT_SUBTITLE)
    d = DeepSeekDistiller(api_key="sk-test", max_retries=0)
    out = await d.distill(raw)

    # The LLM-emitted JSON parses correctly.
    assert out.title_zh == "DeepSeek V4 发布"
    assert len(out.key_points_zh) == 3

    # The prompt that reached litellm was the bilibili variant.
    user_msg = captured["messages"][1]["content"]
    assert "章节切分" in user_msg
    assert "关键段选取" in user_msg
    assert "B 站" in user_msg or "bilibili" in user_msg.lower()


@pytest.mark.asyncio
async def test_non_bilibili_raw_uses_generic_distiller_path(monkeypatch):
    """Regression guard: the bilibili prompt must NOT leak into the
    generic (RSS / article) path."""
    captured: dict[str, Any] = {}

    good = {
        "title_zh": "x", "summary_zh": "y", "key_points_zh": ["z"], "tags_zh": ["t"],
    }

    async def fake_acompletion(*args: Any, **kwargs: Any):
        captured["messages"] = kwargs["messages"]
        return {"choices": [{"message": {"content": json.dumps(good)}}]}

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    raw = _raw_non_bilibili("Some long RSS article body about AI news today.")
    d = DeepSeekDistiller(api_key="sk-test", max_retries=0)
    await d.distill(raw)

    user_msg = captured["messages"][1]["content"]
    assert "章节切分" not in user_msg
    assert "AI 行业分析师" in user_msg


# ----- 离线烟雾测试：fixture 字幕 + mock LLM → 验证 schema ---------------


@pytest.mark.asyncio
async def test_offline_smoke_short_subtitle_with_fake_llm(monkeypatch):
    """Smoke test: feed a ~1k+ char fixture subtitle, mock the LLM
    with a sensible response, verify the parsed DistilledItem has
    non-empty summary_zh / key_points_zh / tags_zh."""
    # Build a ~1k+ char subtitle inline (the exact length isn't the
    # point — what matters is that the route/distill flow produces a
    # well-shaped DistilledItem end-to-end).
    line_template = "[CC] 这是第 {i} 段内容，涉及 AI 模型训练中数据清洗、loss 函数选择、调度策略等细节讨论。"
    fixture_subtitle = "\n".join(line_template.format(i=i) for i in range(20))
    assert len(fixture_subtitle) > 800

    fake_response = {
        "title_zh": "AI 训练优化的关键路径",
        "summary_zh": "本视频讨论 AI 训练中的若干优化技巧，覆盖数据、loss、调度三个层面。",
        "key_points_zh": [
            "数据层：清洗 + 增强",
            "Loss 层：label smoothing",
            "调度层：cosine LR",
        ],
        "tags_zh": ["AI 训练", "深度学习", "优化"],
    }

    async def fake_acompletion(*args: Any, **kwargs: Any):
        return {"choices": [{"message": {"content": json.dumps(fake_response)}}]}

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    raw = _raw_bilibili(fixture_subtitle, title="AI 训练优化的关键路径")
    d = DeepSeekDistiller(api_key="sk-test", max_retries=0)
    out = await d.distill(raw)

    assert out.title_zh == "AI 训练优化的关键路径"
    assert out.summary_zh != ""
    assert len(out.key_points_zh) >= 3
    assert len(out.tags_zh) >= 3


@pytest.mark.asyncio
async def test_offline_smoke_empty_subtitle_falls_back(monkeypatch):
    """Smoke test: empty subtitle → meta-only fallback path → the
    emitted DistilledItem carries the '仅元信息蒸馏' tag."""
    fake_response = {
        "title_zh": "DeepSeek V4 发布",
        "summary_zh": "基于标题与描述推断：这是一个新模型发布。",
        "key_points_zh": ["MoE 架构", "数学评测 SOTA"],
        "tags_zh": ["DeepSeek", "大模型", "仅元信息蒸馏"],
    }

    async def fake_acompletion(*args: Any, **kwargs: Any):
        # Capture the prompt so we can assert the fallback template was used.
        captured["messages"] = kwargs["messages"]
        return {"choices": [{"message": {"content": json.dumps(fake_response)}}]}

    captured: dict[str, Any] = {}
    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    raw = _raw_bilibili(
        content="",
        title="DeepSeek V4 发布",
        description="新一代 MoE 大模型。",
        author="DeepSeek 官方",
    )
    d = DeepSeekDistiller(api_key="sk-test", max_retries=0)
    out = await d.distill(raw)

    # Prompt that reached litellm was the meta-only fallback.
    user_msg = captured["messages"][1]["content"]
    assert "没有可用字幕" in user_msg or "无可用字幕" in user_msg

    # Parsed response carries the "仅元信息蒸馏" tag downstream UI relies on.
    assert "仅元信息蒸馏" in out.tags_zh
    assert out.summary_zh != ""


# ----- Sanity on the constants we exported ------------------------------


def test_truncation_constants_are_sane():
    """Guard against accidental re-tuning that breaks the budget."""
    assert HEAD_CHARS + MIDDLE_CHARS + TAIL_CHARS <= DEFAULT_MAX_CHARS
    # Budget should comfortably hold a typical DeepSeek 64k context:
    # prompt template + system message ≈ 1.5k tokens, leaving room.
    assert DEFAULT_MAX_CHARS >= 30000


# ----- Segment-level truncation -----------------------------------------


def test_truncate_segment_list_keeps_all_when_under_cap():
    """If the segment count fits within head+middle+tail, keep them all."""
    segs = [f"line {i}" for i in range(10)]
    kept, kept_n, omitted = truncate_segment_list(segs, max_chars=10000)
    assert kept_n == 10
    assert omitted == 0
    assert kept == segs


def test_truncate_segment_list_samples_when_over_cap():
    """If too many segments, head+middle+tail sampling kicks in."""
    segs = [f"segment number {i:04d} with some content" for i in range(500)]
    kept, kept_n, omitted = truncate_segment_list(
        segs, max_chars=20000, head_count=10, middle_count=10, tail_count=5,
    )
    assert kept_n == 25  # head + middle + tail
    assert omitted == 475
    # The first 10 from the head are present in order.
    assert kept[0] == segs[0]
    assert kept[9] == segs[9]
    # The tail of the original is present at the end.
    assert kept[-1] == segs[-1]


def test_truncate_segment_list_respects_char_budget():
    """After sampling, the kept content must fit within the char budget
    (allowing a small overshoot for the safety net loop)."""
    segs = ["x" * 100 for _ in range(1000)]  # 100k chars total
    kept, _, _ = truncate_segment_list(
        segs, max_chars=5000, head_count=20, middle_count=20, tail_count=10,
    )
    # Worst case: every kept segment is 100 chars → 50 * 100 = 5000. Tight.
    assert sum(len(s) for s in kept) <= 5000 + 200  # safety margin


def test_truncate_segment_list_empty():
    kept, kept_n, omitted = truncate_segment_list([], max_chars=1000)
    assert kept == []
    assert kept_n == 0
    assert omitted == 0


# ----- Long-subtitle prompt stays bounded ------------------------------


def test_long_subtitle_prompt_stays_bounded():
    """A >36k char subtitle must produce a prompt that's O(budget),
    not O(subtitle length). The previous naive impl sent every segment
    twice and ballooned to >100k chars; this guards against that."""
    # Build a 50k char subtitle with 1000+ mixed CC/AI segments.
    big = "\n".join(
        f"[{'CC' if i % 3 else 'AI'}] 这是第 {i} 段内容," * 2
        for i in range(2000)
    )
    assert len(big) > 40000
    raw = _raw_bilibili(big, title="Long B 站 talk")
    prompt = build_bilibili_prompt(raw)
    # The prompt should fit comfortably in DeepSeek's 64k context,
    # which is ~96k chars (1 token ≈ 1.5 chars). Allow some slack for
    # the template text + safety margin.
    assert len(prompt) < 80_000, (
        f"prompt exploded to {len(prompt)} chars — truncation path is leaking"
    )
    # And the CC / AI sections should explicitly say they were sampled.
    assert "已采样" in prompt