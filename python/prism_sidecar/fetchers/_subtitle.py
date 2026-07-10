"""Shared subtitle → markdown helpers (v0.2c).

Extracted from `fetchers/bilibili.py` so the YouTube fetcher can emit
the exact same cue format. The `- [MM:SS] [CC]/[AI] text` line shape is
a cross-module contract: `distillers/bilibili_prompt.py` keys off the
`[CC]` / `[AI]` prefixes (`_CC_PREFIX_RE` / `_AI_PREFIX_RE`) to split
human vs auto subtitles downstream. Change it in one place only.
"""

from __future__ import annotations

from typing import Any


def format_timestamp(seconds: float) -> str:
    """Float seconds → ``HH:MM:SS`` (hour part only when >= 1h)."""
    s = float(seconds)
    if s < 0:
        s = 0
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def subtitle_body_to_markdown(
    body: list[dict[str, Any]],
    *,
    cue_kind: str = "unknown",
) -> str:
    """Convert ``[{"from": sec, "to": sec, "content": "..."}]`` cues to markdown.

    Each cue becomes one line: ``- [MM:SS] [CC] content`` (or ``[AI]``).
    Consecutive cues with identical ``content`` (AI 字幕常见的"重复行"
    问题,B 站和 YouTube 的自动字幕都有) 被去重。

    ``cue_kind`` is the picked track's provenance ("cc" / "ai" /
    "unknown"). When "unknown" we omit the tag so we don't mislead
    the distiller.
    """
    tag = ""
    if cue_kind == "cc":
        tag = "[CC] "
    elif cue_kind == "ai":
        tag = "[AI] "

    lines: list[str] = []
    last_text: str | None = None
    for cue in body:
        if not isinstance(cue, dict):
            continue
        text = (cue.get("content") or "").strip()
        if not text:
            continue
        if text == last_text:
            # Skip the auto-subtitle's notorious "ghost repeat" line.
            continue
        last_text = text
        ts = format_timestamp(cue.get("from", 0))
        lines.append(f"- [{ts}] {tag}{text}")
    return "\n".join(lines)


__all__ = ["format_timestamp", "subtitle_body_to_markdown"]
