"""DeepSeek distiller — uses litellm to call deepseek-chat.

Why litellm: it gives us one async call interface for OpenAI / Anthropic /
DeepSeek / etc. We can swap to a local model later by changing the model
string.

Configuration:
- Reads `DEEPSEEK_API_KEY` from the env (Tauri is expected to inject it).
- Rate limit: at most 1 request per second (asyncio.Semaphore + delay).
- Retry: 2 attempts with exponential backoff on transient errors.
- 401/403/quota errors raise DistillerKeyInvalid immediately (no retry)
  so we don't burn what little credit a dying key has left.
- On final failure, raises so the pipeline can mark the item as
  `distilled_at=NULL` and move on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from prism_sidecar.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from prism_sidecar.distillers.base import (
    DistilledItem,
    Distiller,
    DistillerKeyInvalid,
    DistillerNotConfigured,
)
from prism_sidecar.fetchers.base import RawItem

log = logging.getLogger(__name__)


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


class DeepSeekDistiller:
    """Distill via DeepSeek (or any OpenAI-compatible model) using litellm."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEEPSEEK_MODEL,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        rate_limit_per_sec: float = 1.0,
    ) -> None:
        self._api_key = api_key or DEEPSEEK_API_KEY
        self._model = model
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._semaphore = asyncio.Semaphore(1)
        self._min_interval = 1.0 / max(rate_limit_per_sec, 0.1)
        self._last_call = 0.0

    async def distill(self, raw: RawItem) -> DistilledItem:
        if not self._api_key:
            raise DistillerNotConfigured(
                "DEEPSEEK_API_KEY is not set; configure it in the Tauri settings"
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

                # litellm reads the provider key from env (DEEPSEEK_API_KEY
                # for the deepseek/ prefix). It's already set by the time
                # we get here.
                response = await litellm.acompletion(
                    model=self._model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout=60,
                )
                content = response["choices"][0]["message"]["content"]
                return _parse_response(content)
            except DistillerNotConfigured:
                raise
            except DistillerKeyInvalid:
                # No retry — a key that worked once won't suddenly start working.
                raise
            except Exception as exc:  # noqa: BLE001
                if _looks_like_key_invalid(exc):
                    raise DistillerKeyInvalid(
                        f"API key rejected by provider ({type(exc).__name__}: {exc}). "
                        "Check the key in Settings (it may be expired, exhausted, or revoked)."
                    ) from exc
                last_exc = exc
                if attempt > self._max_retries:
                    break
                backoff = self._retry_backoff * (2 ** (attempt - 1))
                log.warning(
                    "[distill] attempt %d/%d failed: %s — retry in %.1fs",
                    attempt, self._max_retries + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc


def _looks_like_key_invalid(exc: BaseException) -> bool:
    """Heuristic: detect 401/403/quota/auth errors in litellm / openai / httpx.

    We don't want a flaky network error to be mis-classified as a key
    problem, so we only flag the request as auth-related when the
    underlying provider clearly said so. Tests for these substrings are
    in tests/test_deepseek_distiller.py.
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
    if "insufficient_quota" in msg or "quota exceeded" in msg or "billing" in msg and "limit" in msg:
        return True
    if "credit" in msg and "balance" in msg:
        return True
    return False


__all__ = ["DeepSeekDistiller", "_build_prompt", "_parse_response", "_looks_like_key_invalid"]
