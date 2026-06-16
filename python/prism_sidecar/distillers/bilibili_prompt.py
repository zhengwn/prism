"""B 站长字幕专用蒸馏 prompt + 截断策略。

B 站视频字幕的体量跟 RSS 文章完全不在一个量级：一个 30-60 分钟的
视频，字幕常常是 **1-2 万字**（自动机翻/AI 字幕情况下甚至更长）。
直接把现有通用 :data:`prism_sidecar.distillers.base.PROMPT_TEMPLATE`
丢上去会发生三件事：

1. token 直接打爆（默认 6k 字符 cap 远远不够，留不出 prompt room）
2. 摘要被淹在噪声里——LLM 的注意力会被中段的寒暄/重复段稀释
3. 浪费钱——账单按 token 走

所以 bilibili 单独走一个 prompt，强制 LLM 在调用前先做：

1. **章节切分**——按时间戳 / 主题切 5-10 段
2. **关键段选取**——挑 3-5 段最值得提炼的
3. **C 方案字幕合并**——CC（人工）和 AI（自动机翻）都给了的话，
   LLM 以 CC 为主、AI 为辅校正
4. **fallback**——字幕为空或质量太差，**只用视频元信息（标题+描述）**
   蒸馏，输出加 ``仅元信息蒸馏`` 标记

这个模块是纯函数 + 字符串模板，没有任何 LLM 调用副作用，方便测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from prism_sidecar.fetchers.base import RawItem

# ----- Constants ----------------------------------------------------------

# 截断总预算：~24k token ≈ 36k 中文字符，留出 prompt template + 输出的余量
# 经验上 DeepSeek-Chat 64k context window 是稳的。
DEFAULT_MAX_CHARS = 36000

# 8k + 8k + 4k 截断：开头 + 中段 + 末尾。理由：
# - 开头 8k：通常有开场白、主题背景、嘉宾介绍（高信息密度）
# - 中段 8k：抽 8k "关键段"，按均匀采样避免集中在某个时间段
# - 末尾 4k：通常有结论 / 总结 / 行动号召（信息密度也高）
HEAD_CHARS = 8000
MIDDLE_CHARS = 8000
TAIL_CHARS = 4000


# ----- Source kind detection --------------------------------------------


def is_bilibili(raw: RawItem) -> bool:
    """Return True if this raw item is from a B 站 source.

    Detection priority:
      1. ``raw.metadata["source_kind"] == "bilibili"``（preferred —— pipeline
         在 fetch 时就打了这个 tag）
      2. ``raw.url`` 包含 ``bilibili.com``（fallback —— 给未来手动 import 用）
    """
    meta_kind = (raw.metadata or {}).get("source_kind")
    if isinstance(meta_kind, str) and meta_kind.lower() == "bilibili":
        return True
    url = (raw.url or "").lower()
    return "bilibili.com" in url or "b23.tv" in url


# ----- Subtitle parsing ---------------------------------------------------

# 字幕段落前缀约定（来自 B 站 fetch 后的预处理）：
#   [CC]  人工/官方字幕
#   [AI]  AI 机翻字幕
# 字幕 fetch 模块把每段前面打了这个 tag，让我们后续能做合并策略。
# 如果 fetch 没打，我们也能宽容地从常见时间戳里猜——但首选是显式 tag。
_CC_PREFIX_RE = re.compile(r"^\s*\[CC\]", re.IGNORECASE)
_AI_PREFIX_RE = re.compile(r"^\s*\[AI\]", re.IGNORECASE)


@dataclass(slots=True)
class SubtitleAnalysis:
    """A parsed view of a bilibili raw subtitle blob.

    Attributes:
        cc_segments: lines that came from CC (human/official) subtitles
        ai_segments: lines that came from AI (machine-translated) subtitles
        total_chars: total characters across all segments (post-trim)
        has_cc: True if at least one CC segment was found
        has_ai: True if at least one AI segment was found
    """

    cc_segments: list[str]
    ai_segments: list[str]
    total_chars: int
    has_cc: bool
    has_ai: bool


def parse_subtitle(raw_text: str) -> SubtitleAnalysis:
    """Split a bilibili subtitle blob into CC vs AI segments.

    Heuristic: each line is either tagged with ``[CC]`` / ``[AI]`` prefix,
    or untagged (we treat untagged as AI — the AI auto-subtitle is the
    default fallback on bilibili).

    A "segment" here is one line of subtitle text. We deliberately don't
    try to merge multi-line segments — the LLM is good at handling raw
    lines and the timestamp markers come from the fetcher, not us.

    Empty input is valid — it means "no subtitles" and triggers the
    meta-only fallback path.
    """
    cc: list[str] = []
    ai: list[str] = []
    if not raw_text or not raw_text.strip():
        return SubtitleAnalysis(cc, ai, 0, False, False)

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _CC_PREFIX_RE.match(stripped):
            # Drop the prefix before storing — the LLM doesn't need to
            # see the bookkeeping tag.
            cc.append(_CC_PREFIX_RE.sub("", stripped).strip())
        elif _AI_PREFIX_RE.match(stripped):
            ai.append(_AI_PREFIX_RE.sub("", stripped).strip())
        else:
            # Untagged — assume AI (auto-subtitle default). Keeping it
            # in the AI bucket lets the merge logic still find a
            # CC-first signal if CC segments exist.
            ai.append(stripped)

    total = sum(len(s) for s in cc) + sum(len(s) for s in ai)
    return SubtitleAnalysis(
        cc_segments=cc,
        ai_segments=ai,
        total_chars=total,
        has_cc=bool(cc),
        has_ai=bool(ai),
    )


# ----- Truncation ----------------------------------------------------------


def truncate_subtitle(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Truncate a (potentially huge) subtitle blob to fit the LLM context.

    Strategy: **head + middle-sampled + tail**.

    - First ``HEAD_CHARS`` chars — usually intro / topic framing
    - Middle ``MIDDLE_CHARS`` chars — uniformly sampled to avoid bias
    - Last ``TAIL_CHARS`` chars — usually conclusion / CTA

    We don't just take the first ``max_chars`` because most of the gold
    is in the middle (the intro is often greetings, the outro is "see
    you next time"). Uniform middle sampling is a reasonable proxy for
    "the 3-5 key segments the LLM should pick from" — the LLM prompt
    then re-instructs it to do its own chapter split.

    If the input is already <= ``max_chars``, returns it unchanged.

    The truncation markers (``[... 截断 N 字符 ...]``) are inserted as
    ASCII fences so the LLM can see what we did and not get confused
    by the abrupt gap.
    """
    if not text:
        return text
    if len(text) <= max_chars:
        return text

    budget = max_chars
    head_budget = min(HEAD_CHARS, budget)
    remaining = budget - head_budget

    # Split the remainder between tail and middle; tail usually wins
    # on info density (conclusion).
    tail_budget = min(TAIL_CHARS, remaining // 2)
    middle_budget = max(0, remaining - tail_budget)

    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    if middle_budget > 0:
        # Middle sampling window — leave room on both sides of the
        # head and tail we're already taking.
        body_start = head_budget
        body_end = len(text) - tail_budget if tail_budget > 0 else len(text)
        body_len = body_end - body_start
        if body_len > middle_budget:
            # Uniform step: pick every Nth char from the body.
            step = body_len / middle_budget
            sampled_indexes = [body_start + int(i * step) for i in range(middle_budget)]
            middle = "".join(text[i] for i in sampled_indexes)
        else:
            middle = text[body_start:body_end]
    else:
        middle = ""

    parts = [head]
    if middle:
        parts.append(f"\n\n[... 中段采样 {len(text) - head_budget - tail_budget if tail_budget else len(text) - head_budget} 字符 ...]\n\n")
        parts.append(middle)
    if tail:
        parts.append(f"\n\n[... 末尾省略 ...]\n\n")
        parts.append(tail)
    return "".join(parts)


def truncate_segment_list(
    segments: list[str],
    max_chars: int,
    *,
    head_count: int = 30,
    middle_count: int = 30,
    tail_count: int = 15,
) -> tuple[list[str], int, int]:
    """Greedy sample a segment list down to fit a character budget.

    Strategy: **head + middle-sampled + tail** at the SEGMENT level,
    not the character level. We pick a fixed number of segments from
    the head, the tail, and uniformly sample the middle. The actual
    character count is bounded by the segment lengths, but the
    *number of segments* is bounded by these three constants.

    Returns: (kept_segments, kept_count, omitted_count).
    """
    if not segments:
        return [], 0, 0
    n = len(segments)
    # If we have fewer segments than head+middle+tail, keep them all.
    if n <= head_count + middle_count + tail_count:
        return list(segments), n, 0

    head = segments[:head_count]
    tail = segments[-tail_count:] if tail_count > 0 else []
    middle_window_start = head_count
    middle_window_end = n - tail_count if tail_count > 0 else n
    middle_window_len = middle_window_end - middle_window_start
    if middle_count > 0 and middle_window_len > 0:
        # Uniform step through the middle window.
        if middle_count >= middle_window_len:
            middle = segments[middle_window_start:middle_window_end]
        else:
            step = middle_window_len / middle_count
            sampled_indexes = [
                middle_window_start + int(i * step) for i in range(middle_count)
            ]
            middle = [segments[i] for i in sampled_indexes]
    else:
        middle = []

    kept = list(head) + list(middle) + list(tail)
    # Sanity: enforce budget as a soft check. If we still exceed,
    # drop tail chunks first (head info is most important).
    while sum(len(s) for s in kept) > max_chars and len(kept) > head_count:
        kept.pop()
    return kept, len(kept), n - len(kept)


# ----- Prompt templates ---------------------------------------------------


BILIBILI_DISTILL_PROMPT = """你是一名中文 AI 内容编辑，正在为一档 B 站长视频做精炼摘要。

# 输入材料
- 视频标题：{title_en}
- 视频描述：{description}
- 字幕来源标记：{subtitle_source_note}

# 字幕段落
{subtitle_body}

# 你的工作流程（严格按顺序）
1. **章节切分**：先把整段字幕切分成 5-10 个章节（按主题/论点切，不按固定时间）。
2. **关键段选取**：从 5-10 个章节里挑出 **3-5 个最值得提炼的**——信息密度最高、最有独到观点或具体数据的章节，**忽略寒暄、客套、重复、过渡句**。
3. **基于关键段输出结构化 JSON**。

# 字幕合并策略（C 方案）
如果字幕同时含 CC（人工/官方）和 AI（自动机翻）段落：
- 以 **CC 为主**——优先信任 CC 段落的事实与措辞
- 用 AI 段落做 **辅佐校正**——只在 CC 缺失、CC 模糊、CC 读不通时回退到 AI
- 不要把 AI 段落当成独立信源——它是补充，不是平级

如果只有 AI：直接用 AI，但要在心里记住"AI 可能机翻不准"。

# 输出格式（严格 JSON，**字符串内的双引号必须用反斜杠转义成 \\"，不得使用中文全角引号 " "**）
{{
  "title_zh": "中文标题（保留原标题的产品名/技术名/嘉宾名）",
  "summary_zh": "2-3 句话中文总结，覆盖视频的核心论点或信息",
  "key_points_zh": ["关键观点 1", "关键观点 2", "关键观点 3"],
  "tags_zh": ["标签1", "标签2", "标签3"]
}}

# 约束
- title_zh：≤ 30 字，直白陈述视频主题，不要"今天我们聊聊..."这种口水开场
- summary_zh：2-3 句，覆盖核心观点 / 数据 / 结论
- key_points_zh：3-5 条，每条 1 句话，独立成意，**优先使用视频里出现过的具体名词、数字、人名**
- tags_zh：3-5 个中文标签，覆盖领域 + 子话题 + 关键实体
- 如果字幕里有"独家"、"首次"、"突破"这类强信号词，summary 必须 cover
"""


BILIBILI_META_ONLY_PROMPT = """你是一名中文 AI 内容编辑。**本视频没有可用字幕**（或字幕质量过差不适合蒸馏）。

# 输入材料
- 视频标题：{title_en}
- 视频描述：{description}
- 视频作者 / UP 主：{author}

# 你的工作
**仅基于标题 + 描述** 做最合理的推断式摘要。明确告诉用户这是推断，不要假装你看过内容。

# 输出格式（严格 JSON，**字符串内的双引号必须用反斜杠转义成 \\"，不得使用中文全角引号 " "**）
{{
  "title_zh": "中文标题",
  "summary_zh": "2-3 句话中文总结（明确标注'基于标题与描述推断'）",
  "key_points_zh": ["可从标题/描述推断的要点 1", "可从标题/描述推断的要点 2", "..."],
  "tags_zh": ["标签1", "标签2", "标签3", "仅元信息蒸馏"]
}}

# 约束
- key_points_zh 必须明确可从标题/描述推出，不要编造视频细节
- tags_zh **必须** 含 "仅元信息蒸馏" 作为强信号，让用户知道这条目不是基于真实字幕提炼的
- 如果标题/描述都为空 → 返回 {{"title_zh": "无法提炼", "summary_zh": "无可用字幕或描述", "key_points_zh": [], "tags_zh": ["仅元信息蒸馏", "无法提炼"]}}
"""


# ----- The build function --------------------------------------------------


def _format_subtitle_for_prompt(analysis: SubtitleAnalysis, truncated: str) -> str:
    """Format the subtitle block that goes into the prompt.

    We deliberately keep CC vs AI segments **visually separated** in the
    prompt so the LLM can apply the "CC first, AI supplementary" rule.
    Each section gets a header explaining its provenance.

    Per-source-kind sampling: each kind gets its own head+middle+tail
    pick from ``analysis.{cc,ai}_segments`` (independent of the raw-text
    truncation above). This guarantees the prompt body is O(budget),
    not O(subtitle length), regardless of how many segments exist.
    """
    if not analysis.cc_segments and not analysis.ai_segments:
        return "（无字幕）"

    # Per-kind budgets: CC gets the larger share since it's the primary
    # source. If a kind has fewer segments than its budget can hold,
    # the surplus stays unallocated — no need to redistribute.
    cc_chars_budget = int(DEFAULT_MAX_CHARS * 0.6)
    ai_chars_budget = DEFAULT_MAX_CHARS - cc_chars_budget

    sections: list[str] = []
    if analysis.cc_segments:
        kept, kept_n, omitted_n = truncate_segment_list(
            analysis.cc_segments, cc_chars_budget,
            head_count=40, middle_count=40, tail_count=20,
        )
        joined = "\n".join(f"- {line}" for line in kept)
        header = f"## 人工/官方字幕（CC，{kept_n}/{len(analysis.cc_segments)} 段，优先信任）"
        if omitted_n > 0:
            header += f" — 已采样（head + middle + tail）"
        sections.append(f"{header}\n{joined}")
    if analysis.ai_segments:
        kept, kept_n, omitted_n = truncate_segment_list(
            analysis.ai_segments, ai_chars_budget,
            head_count=25, middle_count=25, tail_count=10,
        )
        joined = "\n".join(f"- {line}" for line in kept)
        header = f"## AI 机翻字幕（AI，{kept_n}/{len(analysis.ai_segments)} 段，辅助校正）"
        if omitted_n > 0:
            header += f" — 已采样（head + middle + tail）"
        sections.append(f"{header}\n{joined}")
    return "\n\n".join(sections)


def _strip_all_prefixes(text: str) -> str:
    """Strip ``[CC]`` / ``[AI]`` prefix from every line of `text`."""
    out_lines: list[str] = []
    for line in text.splitlines():
        out_lines.append(_CC_PREFIX_RE.sub("", _AI_PREFIX_RE.sub("", line)).strip())
    return "\n".join(out_lines)


def _subtitle_source_note(
    analysis: SubtitleAnalysis,
    subtitle_track_count: Optional[int] = None,
) -> str:
    """Build the human-readable subtitle-source note for the prompt.

    `subtitle_track_count` is an optional hint from the fetcher:
    it tells us how many distinct subtitle tracks the upstream video
    had (0 = none, 1 = single track, 2+ = multi-track). When the
    fetcher provides it we add explicit "single-track" / "no-track"
    language so the LLM is not confused by the bare "仅 CC" / "仅 AI"
    labels — those could otherwise look like the fetcher only emitted
    half the data instead of the video genuinely having one track.
    """
    if analysis.has_cc and analysis.has_ai:
        return "CC（人工/官方）+ AI（自动机翻）双源 — 采用 C 方案：以 CC 为主，AI 辅佐校正"
    if analysis.has_cc:
        if subtitle_track_count == 1:
            return "仅 CC（人工/官方字幕），单轨 — 无需合并策略，直接蒸馏"
        return "仅 CC（人工/官方字幕）— 无需合并"
    if analysis.has_ai:
        if subtitle_track_count == 1:
            return "仅 AI（自动机翻字幕），单轨 — 注意可能的机翻失真"
        return "仅 AI（自动机翻字幕）— 注意可能的机翻失真"
    if subtitle_track_count == 0:
        return "无字幕轨道（fetcher 明确返回 0 条字幕）"
    return "无字幕"


def build_bilibili_prompt(
    raw: RawItem,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Pick the right bilibili prompt and assemble it.

    Decision tree:
      - If ``raw.content`` is empty / whitespace-only → fall back to
        the **meta-only** prompt (no subtitle to distill).
      - Otherwise, parse the subtitle, truncate if oversized, and
        assemble the **full** bilibili prompt.

    The meta-only branch is also the path we hit when subtitle quality
    is too poor to be useful — the caller (fetcher / pipeline) can
    detect that and pass ``content=""``.
    """
    content = (raw.content or "").strip()
    description = (raw.metadata or {}).get("description", "")
    if isinstance(description, str):
        description = description.strip()
    else:
        description = ""

    if not content:
        return BILIBILI_META_ONLY_PROMPT.format(
            title_en=raw.title or "",
            description=description or "（无描述）",
            author=raw.author or "（未知）",
        )

    analysis = parse_subtitle(content)
    truncated = truncate_subtitle(content, max_chars=max_chars)
    subtitle_block = _format_subtitle_for_prompt(analysis, truncated)

    # The fetcher sets `subtitle_track_count` in metadata to tell us
    # how many distinct tracks the source video had. We surface this
    # to the LLM only in the single-track / zero-track cases where the
    # ambiguous "仅 CC" / "无字幕" wording could otherwise confuse
    # reasoning. The two-track + both-tags-present case (the C-plan
    # merge) doesn't need this extra signal.
    track_count_meta = (raw.metadata or {}).get("subtitle_track_count")
    track_count: Optional[int] = None
    if isinstance(track_count_meta, int) and track_count_meta >= 0:
        track_count = track_count_meta

    return BILIBILI_DISTILL_PROMPT.format(
        title_en=raw.title or "",
        description=description or "（无描述）",
        subtitle_source_note=_subtitle_source_note(analysis, track_count),
        subtitle_body=subtitle_block,
    )


# ----- Public helpers (re-exported for tests + distiller base) ------------


def should_use_bilibili_prompt(raw: RawItem) -> bool:
    """Decide whether this raw item should go through the bilibili prompt.

    Mirrors :func:`is_bilibili` but exposed under a stable name so the
    ``LitellmDistiller`` base class can call it without importing the
    full set of bilibili utilities.
    """
    return is_bilibili(raw)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "HEAD_CHARS",
    "MIDDLE_CHARS",
    "TAIL_CHARS",
    "BILIBILI_DISTILL_PROMPT",
    "BILIBILI_META_ONLY_PROMPT",
    "SubtitleAnalysis",
    "is_bilibili",
    "parse_subtitle",
    "truncate_subtitle",
    "truncate_segment_list",
    "build_bilibili_prompt",
    "should_use_bilibili_prompt",
]