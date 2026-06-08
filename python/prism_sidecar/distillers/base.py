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
lives on the base class so the 5 providers stay in lock-step.

`LitellmDistiller` is intentionally duck-typed (no ABC) so tests can
build a one-off subclass without going through the ABC machinery.
"""

from __future__ import annotations

import asyncio
import json
import logging
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

返回 JSON 格式：{{"title_zh": "...", "summary_zh": "...", "key_points_zh": [...], "tags_zh": [...]}}
"""


def _build_prompt(raw: RawItem) -> str:
    content = raw.content or raw.title
    # Cap content to keep token usage sane.
    if len(content) > 6000:
        content = content[:6000] + "…"
    return PROMPT_TEMPLATE.format(title_en=raw.title, content=content)


def _parse_response(raw_json: str) -> DistilledItem:
    """Parse the model's JSON output into a DistilledItem.

    Tolerant: if the model returns extra prose around the JSON, find the
    first {...} block and try that.
    """
    text = raw_json.strip()

    # Fast path: whole response is JSON.
    try:
        data = json.loads(text)
        return _to_distilled(data)
    except json.JSONDecodeError:
        pass

    # Fallback: locate the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            return _to_distilled(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"distiller returned non-JSON response: {candidate[:200]!r}") from exc
    raise ValueError("distiller returned no JSON object")


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
    """Shared litellm-backed distiller for all 5 providers.

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
]
