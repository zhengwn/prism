"""Distiller Protocol + DistilledItem + shared litellm implementation.

A `Distiller` takes a `RawItem` and returns a `DistilledItem` containing
the Chinese-language title, summary, key points, and tags. We use a
Protocol so we can swap providers (DeepSeek / OpenAI / Anthropic /
Ollama / Custom) without touching the pipeline.

Architecture
------------
Each concrete distiller subclasses :class:`LitellmDistiller` and only
fills in two things:

* the litellm model string (e.g. ``"deepseek/deepseek-chat"``,
  ``"openai/gpt-4o-mini"``, ``"ollama/qwen2.5:7b"``)
* how the API key is sourced (env var name, or ``None`` for keyless
  providers like Ollama)

Everything else — the prompt, JSON parsing, retry loop, 401 detection —
lives on the base class so every registered provider stays in lock-step.
(v0.2b pruned the active provider list down to 2 — DeepSeek + MiniMax,
see `distillers/registry.py:PROVIDERS` — but the base class stays
provider-count-agnostic by design, so re-adding a third litellm-backed
provider is still a small, self-contained change.)

`LitellmDistiller` is intentionally duck-typed (no ABC) so tests can
build a one-off subclass without going through the ABC machinery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from prism_sidecar.fetchers.base import RawItem

log = logging.getLogger(__name__)


# ----- Result & exceptions ------------------------------------------------


@dataclass(slots=True)
class DistilledItem:
    """The structured output of one distillation call."""

    title_zh: str
    summary_zh: str
    key_points_zh: list[str] = field(default_factory=list)
    tags_zh: list[str] = field(default_factory=list)


class DistillerNotConfigured(RuntimeError):
    """Raised when a Distiller is invoked without the required credentials."""


class DistillerKeyInvalid(RuntimeError):
    """Raised when the configured API key is rejected (401/403/quota/expired).

    Distinct from DistillerNotConfigured: a key is *present* but the
    provider says it's no good. Callers should stop the whole batch
    immediately (not retry) so we don't burn through what little credit
    the key may have left.
    """


@runtime_checkable
class Distiller(Protocol):
    """A pluggable LLM-backed distiller."""

    async def distill(self, raw: RawItem) -> DistilledItem:
        ...


# ----- Prompt + response helpers (shared by all providers) ----------------

PROMPT_TEMPLATE = """你是一名 AI 行业分析师。请把以下内容提炼成结构化知识单元。
要求：
1. title_zh：翻译为中文标题（保留产品名/技术名原文）
2. summary_zh：用 2-3 句话中文总结核心信息
3. key_points_zh：3-5 条中文关键点
4. tags_zh：3-5 个中文标签（如"大模型"、"开源"、"工具"等）

原文标题：{title_en}
原文内容：{content}

严格返回 JSON（必须是合法 JSON，**字符串内的双引号必须用反斜杠转义成 \\"，不得使用中文全角引号 " "**）。
格式示例：
{{"title_zh": "...", "summary_zh": "...", "key_points_zh": [...], "tags_zh": [...]}}
"""


# X (Twitter) is short-form: a single tweet or a thread stitched together
# by the bridge. The general template assumes an article and tends to pad
# a 280-char post into a bloated "summary". This prompt keeps it tight and
# thread-aware. Wired in `_build_prompt` via feed_kind == "x" (v0.2c).
X_PROMPT_TEMPLATE = """你是一名 AI 行业分析师。下面是来自 X（推特）的短文本内容（可能是单条推文，也可能是一个 thread 拼接而成）。请提炼成结构化知识单元。
注意：内容很短，不要脑补或夸大；如果是 thread，请综合整条线索的核心观点。
要求：
1. title_zh：用一句中文概括这条推文/thread 的核心（保留产品名/技术名原文；不要照搬 @用户名）
2. summary_zh：1-2 句中文总结，只保留真正的信息点，忽略寒暄和 hashtag 堆砌
3. key_points_zh：1-3 条中文关键点（内容确实很短时给 1 条也可以）
4. tags_zh：2-4 个中文标签

作者：{author}
原文内容：{content}

严格返回 JSON（必须是合法 JSON，**字符串内的双引号必须用反斜杠转义成 \\"，不得使用中文全角引号 " "**）。
格式示例：
{{"title_zh": "...", "summary_zh": "...", "key_points_zh": [...], "tags_zh": [...]}}
"""


def _is_x(raw: RawItem) -> bool:
    """True for X/Twitter items (feed_kind stamped by XFetcher)."""
    meta_kind = (raw.metadata or {}).get("feed_kind")
    return isinstance(meta_kind, str) and meta_kind.lower() == "x"


def _build_prompt(raw: RawItem) -> str:
    # B 站长字幕走专属 prompt + 截断策略（v0.2c+）。其他 source kind
    # 继续走原来的通用模板。
    # 延迟 import 避免 bilibili_prompt 反向引用 base。
    from prism_sidecar.distillers.bilibili_prompt import (
        build_bilibili_prompt,
        should_use_bilibili_prompt,
    )
    if should_use_bilibili_prompt(raw):
        return build_bilibili_prompt(raw)
    content = raw.content or raw.title
    # Cap content to keep token usage sane.
    if len(content) > 6000:
        content = content[:6000] + "…"
    if _is_x(raw):
        return X_PROMPT_TEMPLATE.format(
            author=raw.author or (raw.metadata or {}).get("handle") or "—",
            content=content,
        )
    return PROMPT_TEMPLATE.format(title_en=raw.title, content=content)


# ----- Response-shape rescue helpers ---------------------------------------
#
# Chinese-language LLMs (DeepSeek, MiniMax / M3, Qwen, GLM…) have a habit
# of "helping" by replacing ASCII double-quotes inside JSON string values
# with the full-width Chinese ones (U+201C / U+201D). That makes the
# response a syntactic nightmare for `json.loads` even though the model
# is semantically returning the right thing. We see this in the wild as:
#
#   {"summary_zh": "… 他认为"诚意"是…"}
#                            ^ ASCII " inside what should be a quoted string
#
# We do best-effort rescue in this order:
#   1. parse the raw text as-is
#   2. try a markdown ```json ... ``` fence extract
#   3. try the outermost {...} slice
#   4. try the same with full-width Chinese quotes swapped back to ASCII
#   5. give up
#
# Steps 2 + 4 together handle the two failure modes we see in production
# logs; if a future model adds a third we can extend the chain without
# touching the call sites.

# Matches a markdown-fenced JSON block: ```json\n...\n``` (the language
# tag is optional; we accept ```\n...\n``` too).
_FENCED_JSON_RE = re.compile(
    r"```(?:json|JSON)?\s*\n([\s\S]*?)\n```",
    re.MULTILINE,
)
# Matches ASCII " sandwiched between two CJK characters (or a CJK char
# and a common opener/closer). These are the "smuggled" full-width-looking
# quotes the model writes instead of escaping. Replacing them with the
# actual full-width U+201C / U+201D makes the response parseable; the
# string content is unchanged from the human's point of view.
_CJK_BETWEEN_QUOTES_RE = re.compile(
    r'(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])"(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])'
)


def _swap_chinese_quotes(text: str) -> str:
    """Replace ASCII " between CJK characters with U+201C / U+201D.

    Walks the matches left-to-right and alternates open/close so that
    a sequence like  汉字"开头"和"结尾"汉字  becomes
    汉字"开头"和"结尾"汉字  — the natural reading pair in Chinese
    typography. Symmetric (all "): the alternation ensures every other
    match opens the next pair.
    """
    out: list[str] = []
    cursor = 0
    open_quote = True
    for m in _CJK_BETWEEN_QUOTES_RE.finditer(text):
        out.append(text[cursor:m.start()])
        out.append("\u201c" if open_quote else "\u201d")
        open_quote = not open_quote
        cursor = m.end()
    out.append(text[cursor:])
    return "".join(out)


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` substring, or None.

    Naive `text[text.find("{"):text.rfind("}")+1]` can be fooled by
    ``{`` inside a string value (e.g. a key_points_zh list with a brace
    in the literal text) — we do a small state machine to find a real
    balanced object. Still not a full JSON parser, but enough for the
    rescue path.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _try_parse(text: str) -> Optional[dict]:
    """Attempt to parse `text` as JSON, returning a dict or None.

    Used by `_parse_response` as the inner step of each rescue attempt.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_response(raw_json: str) -> DistilledItem:
    """Parse the model's JSON output into a DistilledItem.

    Tolerant, in order:

    1. Parse the whole stripped response as JSON.
    2. Extract a ```` ```json ... ``` ```` fenced block.
    3. Slice the first balanced ``{...}`` substring.
    4. Same as (3) but with full-width Chinese quotes swapped back in
       for ASCII quotes that are sandwiched between CJK characters
       (the model's "I'll just use smart quotes" habit).
    5. Raise with a sample of the response so the retry log is useful.
    """
    text = raw_json.strip()
    if not text:
        raise ValueError("distiller returned empty response")

    # --- (1) raw parse
    data = _try_parse(text)
    if data is not None:
        return _to_distilled(data)

    # --- (2) markdown fence
    for fence in _FENCED_JSON_RE.findall(text):
        data = _try_parse(fence.strip())
        if data is not None:
            return _to_distilled(data)

    # --- (3) first balanced {...} slice
    candidate = _extract_balanced_json_object(text)
    if candidate is not None:
        data = _try_parse(candidate)
        if data is not None:
            return _to_distilled(data)

        # --- (4) same slice with full-width quote rescue
        rescued = _swap_chinese_quotes(candidate)
        if rescued != candidate:
            data = _try_parse(rescued)
            if data is not None:
                log.info(
                    "[distiller] recovered from CJK-smart-quote JSON "
                    "(len=%d, swaps applied)",
                    len(rescued) - len(candidate),
                )
                return _to_distilled(data)

    raise ValueError(
        f"distiller returned non-JSON response: {text[:200]!r}"
    )


def _to_distilled(data: Any) -> DistilledItem:
    if not isinstance(data, dict):
        raise ValueError(f"expected dict, got {type(data).__name__}")
    title_zh = (data.get("title_zh") or "").strip()
    summary_zh = (data.get("summary_zh") or "").strip()
    if not title_zh:
        raise ValueError("missing title_zh")
    if not summary_zh:
        raise ValueError("missing summary_zh")
    key_points = data.get("key_points_zh") or []
    if not isinstance(key_points, list):
        key_points = [str(key_points)]
    tags = data.get("tags_zh") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    return DistilledItem(
        title_zh=str(title_zh),
        summary_zh=str(summary_zh),
        key_points_zh=[str(x) for x in key_points if str(x).strip()],
        tags_zh=[str(x) for x in tags if str(x).strip()],
    )


def looks_like_key_invalid(exc: BaseException) -> bool:
    """Heuristic: detect 401/403/quota/auth errors in litellm / openai / httpx.

    We don't want a flaky network error to be mis-classified as a key
    problem, so we only flag the request as auth-related when the
    underlying provider clearly said so.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "authenticationerror" in name or "permissionerror" in name:
        return True
    if "unauthorized" in name or "forbidden" in name:
        return True
    if "status 401" in msg or "status 403" in msg:
        return True
    if "invalid api key" in msg or "incorrect api key" in msg:
        return True
    if "insufficient_quota" in msg or "quota exceeded" in msg:
        return True
    if "billing" in msg and "limit" in msg:
        return True
    if "credit" in msg and "balance" in msg:
        return True
    return False


# Backwards-compat alias — old code imported `_looks_like_key_invalid`
# from the deepseek module.
_looks_like_key_invalid = looks_like_key_invalid


# ----- LitellmDistiller base class ----------------------------------------


class LitellmDistiller:
    """Shared litellm-backed distiller for every registered provider
    (currently DeepSeek + MiniMax — see `distillers/registry.py`).

    Subclasses must set the class attributes (or override the instance
    attributes in ``__init__``):

    * ``provider_name: str`` — short id used in log messages (e.g. "deepseek")
    * ``default_model: str`` — the litellm model string for this provider
    * ``env_key_var: str | None`` — env var that holds the API key, or
      ``None`` for keyless providers (Ollama).  The base class reads it
      once at construction.

    For providers that need an extra knob (e.g. Ollama's ``api_base`` or
    Custom's ``base_url``), subclasses may override
    :meth:`_extra_litellm_kwargs` to inject it.
    """

    # Override in subclasses.
    provider_name: str = "litellm"
    default_model: str = ""
    env_key_var: str | None = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        rate_limit_per_sec: float = 1.0,
        **extra: Any,
    ) -> None:
        if self.env_key_var is not None:
            import os
            resolved_key = api_key or os.environ.get(self.env_key_var) or None
        else:
            # Keyless provider (e.g. Ollama) — api_key stays None.
            resolved_key = None
        self._api_key = resolved_key
        self._model = model or self.default_model
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._extra = extra
        self._semaphore = asyncio.Semaphore(1)
        self._min_interval = 1.0 / max(rate_limit_per_sec, 0.1)
        self._last_call = 0.0

    # ---- subclass extension point -------------------------------------

    def _extra_litellm_kwargs(self) -> dict[str, Any]:
        """Override to pass provider-specific args to ``litellm.acompletion``.

        The base implementation forwards anything captured in
        ``**extra`` at construction. Subclasses (Ollama, Custom) also
        inject ``api_base`` here.
        """
        return dict(self._extra)

    # ---- the Distiller protocol ---------------------------------------

    async def distill(self, raw: RawItem) -> DistilledItem:
        # Keyless providers (Ollama) never raise DistillerNotConfigured.
        if self.env_key_var is not None and not self._api_key:
            raise DistillerNotConfigured(
                f"{self.env_key_var} is not set; configure it in the Tauri settings"
            )

        prompt = _build_prompt(raw)
        messages = [
            {
                "role": "system",
                "content": "你是一名 AI 行业分析师，输出必须是合法的 JSON。",
            },
            {"role": "user", "content": prompt},
        ]

        # Rate limit: at most N calls per second.
        async with self._semaphore:
            await self._pace()
            try:
                return await self._call_with_retry(messages)
            finally:
                self._last_call = asyncio.get_event_loop().time()

    # ---- internals -----------------------------------------------------

    async def _pace(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._last_call
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

    async def _call_with_retry(self, messages: list[dict[str, str]]) -> DistilledItem:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                import litellm  # imported lazily so unit tests can mock it

                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "timeout": 60,
                }
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                kwargs.update(self._extra_litellm_kwargs())

                response = await litellm.acompletion(**kwargs)
                content = response["choices"][0]["message"]["content"]
                return _parse_response(content)
            except DistillerNotConfigured:
                raise
            except DistillerKeyInvalid:
                # No retry — a key that worked once won't suddenly start working.
                raise
            except Exception as exc:  # noqa: BLE001
                if looks_like_key_invalid(exc):
                    raise DistillerKeyInvalid(
                        f"API key rejected by {self.provider_name} "
                        f"({type(exc).__name__}: {exc}). "
                        "Check the key in Settings (it may be expired, "
                        "exhausted, or revoked)."
                    ) from exc
                last_exc = exc
                if attempt > self._max_retries:
                    break
                backoff = self._retry_backoff * (2 ** (attempt - 1))
                log.warning(
                    "[%s] attempt %d/%d failed: %s — retry in %.1fs",
                    self.provider_name, attempt, self._max_retries + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc


__all__ = [
    "Distiller",
    "DistilledItem",
    "DistillerNotConfigured",
    "DistillerKeyInvalid",
    "LitellmDistiller",
    "looks_like_key_invalid",
    "_looks_like_key_invalid",  # back-compat
    "_build_prompt",
    "_parse_response",
    "_swap_chinese_quotes",
    "_extract_balanced_json_object",
]
