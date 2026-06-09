"""Test the DeepSeek distiller with mocked litellm.

The shared ``LitellmDistiller`` base class is the meat of the test
target now — every concrete provider subclasses it. The provider-
specific bits we still test here are the model string and the
DEEPSEEK_API_KEY env var lookup.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.base import (
    DistillerNotConfigured,
    _build_prompt,
    _looks_like_key_invalid,
    _parse_response,
    looks_like_key_invalid,
)
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


SAMPLE_RAW = RawItem(
    url="https://example.com/post",
    title="OpenAI ships GPT-5",
    content="OpenAI released GPT-5 today, a new multimodal model with native tool use.",
    published_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
    author="OpenAI",
    content_type=ContentType.article,
)


SAMPLE_GOOD = {
    "title_zh": "OpenAI 发布 GPT-5",
    "summary_zh": "OpenAI 今日发布 GPT-5，原生多模态并支持工具调用。",
    "key_points_zh": ["统一多模态架构", "原生工具调用", "性能比 GPT-4o 提升 50%"],
    "tags_zh": ["openai", "gpt-5", "多模态", "大模型"],
}


# ---- prompt + parsing helpers (live in base now) ------------------------


def test_build_prompt_includes_title_and_content():
    prompt = _build_prompt(SAMPLE_RAW)
    assert "OpenAI ships GPT-5" in prompt
    assert "GPT-5 today" in prompt
    assert "title_zh" in prompt
    assert "key_points_zh" in prompt


def test_build_prompt_truncates_long_content():
    raw = RawItem(
        url="x", title="t", content="a" * 10000,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    p = _build_prompt(raw)
    # 6000 char cap + ellipsis, plus title etc.
    assert len(p) < 8000


def test_parse_response_pure_json():
    out = _parse_response(json.dumps(SAMPLE_GOOD))
    assert out.title_zh == "OpenAI 发布 GPT-5"
    assert "GPT-5" in out.summary_zh
    assert len(out.key_points_zh) == 3
    assert len(out.tags_zh) == 4


def test_parse_response_handles_surrounding_prose():
    wrapped = "Here is the answer:\n" + json.dumps(SAMPLE_GOOD) + "\nDone."
    out = _parse_response(wrapped)
    assert out.title_zh == "OpenAI 发布 GPT-5"


def test_parse_response_rejects_missing_fields():
    with pytest.raises(ValueError):
        _parse_response(json.dumps({"title_zh": "ok", "summary_zh": ""}))


def test_parse_response_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_response("not json at all")


# ---- DeepSeek-specific: class wiring + env var name ----------------------


def test_deepseek_default_model_string():
    """The litellm model string must use the deepseek/ prefix and the
    pinned deepseek-v4-pro model id."""
    d = DeepSeekDistiller(api_key="sk-test", max_retries=0)
    assert d._model == "deepseek/deepseek-v4-pro"


def test_deepseek_reads_key_from_env(monkeypatch):
    """If no api_key is passed, the base class reads DEEPSEEK_API_KEY."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    d = DeepSeekDistiller()
    assert d._api_key == "sk-from-env"


# ---- No-key handling -----------------------------------------------------


def test_distiller_raises_when_no_key():
    d = DeepSeekDistiller(api_key=None, max_retries=0)
    with pytest.raises(DistillerNotConfigured):
        import asyncio
        asyncio.run(d.distill(SAMPLE_RAW))


# ---- litellm call shape -------------------------------------------------


@pytest.mark.asyncio
async def test_distiller_calls_litellm_and_parses(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_acompletion(*args: Any, **kwargs: Any):
        captured["kwargs"] = kwargs
        return {
            "choices": [
                {"message": {"content": json.dumps(SAMPLE_GOOD)}}
            ]
        }

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    d = DeepSeekDistiller(api_key="sk-test", max_retries=0)
    out = await d.distill(SAMPLE_RAW)

    assert out.title_zh == "OpenAI 发布 GPT-5"
    assert captured["kwargs"]["model"] == "deepseek/deepseek-v4-pro"
    assert captured["kwargs"]["api_key"] == "sk-test"
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}


# ---- key-invalid detection (moved to base) ------------------------------


class _FakeAuthError(Exception):
    """Stand-in for litellm's AuthenticationError / PermissionError."""


class _FakeRateLimitError(Exception):
    """Stand-in for a transient 429 (NOT a key problem)."""


def test_looks_like_key_invalid_detects_auth_substrings():
    # What an actual litellm/openai error usually looks like in production.
    assert _looks_like_key_invalid(_FakeAuthError("status 401 unauthorized"))
    assert _looks_like_key_invalid(_FakeAuthError("status 403 forbidden"))
    assert _looks_like_key_invalid(_FakeAuthError("invalid api key"))
    assert _looks_like_key_invalid(_FakeAuthError("incorrect api key provided"))
    assert _looks_like_key_invalid(_FakeAuthError("insufficient_quota: you have used all credits"))
    assert _looks_like_key_invalid(_FakeAuthError("quota exceeded for this billing period"))


def test_looks_like_key_invalid_ignores_transient_errors():
    # 429 (rate limit) and 5xx (server error) are NOT key problems.
    assert not _looks_like_key_invalid(_FakeRateLimitError("status 429 rate limit hit"))
    assert not _looks_like_key_invalid(RuntimeError("status 500 internal server error"))
    assert not _looks_like_key_invalid(RuntimeError("connection timeout"))


def test_looks_like_key_invalid_public_alias():
    """The public name and the private alias should be the same function."""
    assert looks_like_key_invalid is _looks_like_key_invalid


# ---- 401 fast-fail via the base class -----------------------------------


@pytest.mark.asyncio
async def test_distill_raises_KeyInvalid_on_401_no_retry(monkeypatch):
    """A 401 from the provider should raise DistillerKeyInvalid
    immediately, without burning retry attempts on a dead key."""
    from prism_sidecar.distillers.base import DistillerKeyInvalid

    call_count = {"n": 0}

    async def fake_acompletion(*args: Any, **kwargs: Any):
        call_count["n"] += 1
        raise _FakeAuthError("status 401 unauthorized: invalid api key")

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    d = DeepSeekDistiller(api_key="sk-dead", max_retries=3)  # 3 retries, but should not be used
    with pytest.raises(DistillerKeyInvalid):
        await d.distill(SAMPLE_RAW)
    # No retries on auth errors — the provider already said "no".
    assert call_count["n"] == 1
