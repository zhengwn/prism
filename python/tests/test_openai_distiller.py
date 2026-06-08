"""Test the OpenAI distiller with mocked litellm.

Verifies the model string, the api_key passthrough, and the 401
fast-fail path shared by every LitellmDistiller subclass.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.base import (
    DistillerKeyInvalid,
    DistillerNotConfigured,
)
from prism_sidecar.distillers.openai import OpenAIDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


SAMPLE_RAW = RawItem(
    url="https://example.com/p",
    title="GPT-4o mini",
    content="OpenAI released GPT-4o mini.",
    published_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    author="OpenAI",
    content_type=ContentType.article,
)


SAMPLE_GOOD = {
    "title_zh": "OpenAI 发布 GPT-4o mini",
    "summary_zh": "便宜的小模型",
    "key_points_zh": ["便宜", "快"],
    "tags_zh": ["openai", "gpt-4o-mini"],
}


def test_openai_default_model():
    d = OpenAIDistiller(api_key="sk-test", max_retries=0)
    assert d._model == "openai/gpt-4o-mini"


def test_openai_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    d = OpenAIDistiller()
    assert d._api_key == "sk-env"


def test_openai_raises_when_no_key():
    d = OpenAIDistiller(api_key=None, max_retries=0)
    with pytest.raises(DistillerNotConfigured):
        import asyncio
        asyncio.run(d.distill(SAMPLE_RAW))


@pytest.mark.asyncio
async def test_openai_calls_litellm_with_correct_kwargs(monkeypatch):
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

    d = OpenAIDistiller(api_key="sk-test", max_retries=0)
    out = await d.distill(SAMPLE_RAW)

    assert out.title_zh == "OpenAI 发布 GPT-4o mini"
    assert captured["kwargs"]["model"] == "openai/gpt-4o-mini"
    assert captured["kwargs"]["api_key"] == "sk-test"
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_401_raises_keyinvalid_no_retry(monkeypatch):
    class _AuthErr(Exception):
        pass

    call_count = {"n": 0}

    async def fake_acompletion(*args: Any, **kwargs: Any):
        call_count["n"] += 1
        raise _AuthErr("status 401 unauthorized: invalid api key")

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    d = OpenAIDistiller(api_key="sk-dead", max_retries=3)
    with pytest.raises(DistillerKeyInvalid):
        await d.distill(SAMPLE_RAW)
    assert call_count["n"] == 1
