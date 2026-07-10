"""离线工具：构造一个真实长度的 B 站字幕 fixture，渲染 prompt，
并把 (a) prompt 前 300 字 (b) 截断后的中段/末尾标记 (c) 完整长度
报告写到 plan workspace 的 smoke-output.json。

不调任何 LLM，纯本地函数验证。

跑法（在 python/ 目录）：
    uv run python scripts/render_realistic_prompt.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from any cwd.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from prism_sidecar.fetchers.base import RawItem  # noqa: E402
from prism_sidecar.distillers.bilibili_prompt import (  # noqa: E402
    BILIBILI_DISTILL_PROMPT,
    DEFAULT_MAX_CHARS,
    HEAD_CHARS,
    MIDDLE_CHARS,
    TAIL_CHARS,
    build_bilibili_prompt,
    parse_subtitle,
    truncate_subtitle,
)


# A 5-chapter "talk on transformer scaling laws" — fabricated but
# representative of the kind of structure real B站 content creators use.
CHAPTERS = [
    ("开场 + 嘉宾介绍", [
        "大家好,欢迎来到今天的 AI 圆桌,我是主持人小明。",
        "今天我们请到了清华的周老师,以及 HuggingFace 的中国区负责人 Emily。",
        "本期话题:大模型的 scaling law 还成立吗?",
        "周老师在最近一篇论文里提出了一个新的假设,我们会详细讨论。",
    ]),
    ("scaling law 背景回顾", [
        "先回顾一下 scaling law 的历史,最早是 OpenAI 的 Kaplan 在 2020 年提出。",
        "后来 Chinchilla 论文修正了这个规律,认为数据和参数应该同步放大。",
        "DeepSeek 的 V4 论文里也引用了这个框架。",
        "核心结论是:在固定算力下,最优的模型大小和数据集大小有一个固定的比例。",
        "过去三年大家都在 follow 这个 ratio 来做训练预算分配。",
    ]),
    ("周老师的新假设", [
        "周老师在 arXiv 上挂了一篇 paper,题目是 Scaling Law Meets Data Quality。",
        "核心观点是:数据质量足够高的时候,模型可以更小但仍然达到相同能力。",
        "具体来说,他们用 7B 的模型 + 高质量数据,做到了和 70B 模型相当的 performance。",
        "这个 7B 模型在 MMLU 上拿到 78 分,接近 Llama-3-70B 的水平。",
        "关键 trick 是 curriculum learning + 严格的 deduplication。",
        "他们用 GPT-4 当 judge 来做数据过滤,过滤掉了大约 40% 的原始数据。",
    ]),
    ("Emily 的工业界视角", [
        "Emily 从 HuggingFace 的角度补充:工业界最关心的其实是 inference cost。",
        "一个 7B 模型 + 高质量数据,可能比 70B 模型便宜 10 倍。",
        "但是 7B 模型在 reasoning 任务上仍然有上限。",
        "所以实际部署里大家会用 cascade:小模型先过一遍,难的题目再路由到大模型。",
        "HuggingFace 的 TGI 框架现在原生支持这种 cascade routing。",
    ]),
    ("圆桌讨论 + 结论", [
        "主持人:那未来一年我们该 follow 哪个路线?",
        "周老师:我觉得 scaling law 仍然成立,但是要乘以一个 quality factor。",
        "Emily:同意,工业界会更激进地往 7B-13B 段发力。",
        "主持人:那 data pipeline 会变成新的护城河?",
        "周老师:对,谁能做好 data filtering,谁就能赢。",
        "本期就到这里,感谢收看,下期我们会聊 agentic workflow 的最新进展。",
    ]),
]


def build_realistic_subtitle() -> str:
    """Build a >36k char subtitle with CC + AI + plain tags.

    We replicate the chapter skeleton many times so the subtitle is
    realistic AND over-budget. Real B站 60-min videos land in the
    15-25k char range; we go a bit higher to verify the truncation
    path is hit.

    Realistic structure:
      - ~70% CC (人工/官方字幕)
      - ~25% AI (自动机翻字幕,通常是英中)
      - ~5% untagged (默认按 AI 处理)
    """
    lines: list[str] = []
    # Repeat the chapter skeleton 60x → ~37k chars (each segment is
    # ~70 chars, 5 chapters × ~7 segs × 60 reps = ~2100 lines).
    for repeat in range(60):
        if repeat > 0:
            lines.append(f"[CC] === 第 {repeat + 1} 段讨论开始 ===")
        for chapter_title, segs in CHAPTERS:
            for i, seg in enumerate(segs):
                if i % 4 == 3:
                    lines.append(f"[AI] {seg}")
                else:
                    lines.append(f"[CC] {seg}")
            lines.append("现场观众:这个问题我也想问")
            lines.append("主持人:好问题")
    return "\n".join(lines)


def main() -> None:
    content = build_realistic_subtitle()
    print(f"[smoke] subtitle length: {len(content)} chars")
    print(f"[smoke] budget:          {DEFAULT_MAX_CHARS} chars ({HEAD_CHARS} + {MIDDLE_CHARS} + {TAIL_CHARS})")

    raw = RawItem(
        url="https://www.bilibili.com/video/BV1SmokeTest",
        title="Scaling Law 还成立吗?周老师 vs Emily 圆桌",
        content=content,
        published_at=datetime(2026, 6, 16, 18, 0, tzinfo=timezone.utc),
        author="AI 圆桌官方",
        content_type=__import__("prism_sidecar.models", fromlist=["ContentType"]).ContentType.video,
        metadata={
            "feed_kind": "bilibili",
            "description": "本期话题:大模型的 scaling law 还成立吗?周老师在最近一篇论文里提出了一个新的假设,Emily 从 HuggingFace 的工业界视角回应。",
        },
    )

    analysis = parse_subtitle(content)
    print(f"[smoke] CC segments: {len(analysis.cc_segments)}, AI segments: {len(analysis.ai_segments)}")
    print(f"[smoke] total chars after parse: {analysis.total_chars}")

    truncated = truncate_subtitle(content)
    print(f"[smoke] truncated length: {len(truncated)} chars")
    print(f"[smoke] head/middle/tail budget hit: {HEAD_CHARS}/{MIDDLE_CHARS}/{TAIL_CHARS}")

    prompt = build_bilibili_prompt(raw)
    print(f"[smoke] rendered prompt length: {len(prompt)} chars")

    out_dir = Path("/Users/zhengweining/.mavis/plans/plan_b679edfb/outputs/sidecar-bilibili-distiller")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "subtitle_chars": len(content),
        "truncated_chars": len(truncated),
        "prompt_chars": len(prompt),
        "cc_segments": len(analysis.cc_segments),
        "ai_segments": len(analysis.ai_segments),
        "budget": {
            "head": HEAD_CHARS, "middle": MIDDLE_CHARS, "tail": TAIL_CHARS,
            "total": DEFAULT_MAX_CHARS,
        },
        "prompt_head_300_chars": prompt[:300],
        "prompt_contains_sampling_marker": {
            "已采样": "已采样" in prompt,
            "CC 优先": "优先信任" in prompt,
            "AI 辅助": "辅助校正" in prompt,
        },
    }
    (out_dir / "smoke-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[smoke] wrote {out_dir / 'smoke-report.json'}")

    # Also write the full prompt to a side file so verifier / humans
    # can inspect it.
    (out_dir / "smoke-prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"[smoke] wrote {out_dir / 'smoke-prompt.txt'} ({len(prompt)} chars)")


if __name__ == "__main__":
    main()