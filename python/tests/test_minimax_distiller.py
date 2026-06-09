"""Test the MiniMax (M3) distiller with mocked litellm.

Verifies the OpenAI-compatible wrapping (model string prefixed with
``openai/``), the api_key passthrough + env-var fallback, the
``api_base`` default + override, and the 401 fast-fail path shared by
every LitellmDistiller subclass.
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
from prism_sidecar.distillers.minimax import MiniMaxDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


SAMPLE_RAW = RawItem(
    url="https://example.com/p",
    title="M3 multimodal release",
    content="MiniMax M3 launched with 1M context and native multimodality.",
    published_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    author="MiniMax",
    content_type=ContentType.article,
)


SAMPLE_GOOD = {
    "title_zh": "MiniMax 发布 M3",
    "summary_zh": "M3 支持 1M 上下文与原生多模态。",
    "key_points_zh": ["1M 上下文", "原生多模态", "OpenAI 兼容协议"],
    "tags_zh": ["MiniMax", "M3", "大模型"],
}


# ---- class wiring + default values ---------------------------------------


def test_minimax_default_model_is_openai_prefixed():
    d = MiniMaxDistiller(api_key="sk-test", max_retries=0)
    # The litellm model string uses the openai/ prefix so the request
    # routes through the OpenAI-compatible adapter.
    assert d._model == "openai/MiniMax-M3"


def test_minimax_default_base_url():
    d = MiniMaxDistiller(api_key="sk-test", max_retries=0)
    assert d._api_base == "https://api.minimaxi.com/v1"


def test_minimax_explicit_base_url_wins():
    d = MiniMaxDistiller(api_key="sk-test", api_base="https://mirror.example/v1")
    assert d._api_base == "https://mirror.example/v1"


def test_minimax_reads_base_url_from_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_BASE", "https://env-host/v1")
    d = MiniMaxDistiller(api_key="sk-test")
    assert d._api_base == "https://env-host/v1"


def test_minimax_wraps_bare_model_name_in_openai_prefix():
    d = MiniMaxDistiller(api_key="sk-test", model="M3-highspeed")
    assert d._model == "openai/M3-highspeed"


def test_minimax_preserves_already_prefixed_model():
    d = MiniMaxDistiller(api_key="sk-test", model="openai/zz")
    assert d._model == "openai/zz"


def test_minimax_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "ey-from-env")
    d = MiniMaxDistiller()
    assert d._api_key == "ey-from-env"


def test_minimax_raises_when_no_key():
    d = MiniMaxDistiller(api_key=None, max_retries=0)
    with pytest.raises(DistillerNotConfigured):
        import asyncio
        asyncio.run(d.distill(SAMPLE_RAW))


# ---- litellm call shape -------------------------------------------------


@pytest.mark.asyncio
async def test_minimax_calls_litellm_with_openai_prefix_and_api_base(monkeypatch):
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

    d = MiniMaxDistiller(
        api_key="sk-test",
        model="M3-highspeed",
        max_retries=0,
    )
    out = await d.distill(SAMPLE_RAW)

    assert out.title_zh == "MiniMax 发布 M3"
    assert captured["kwargs"]["model"] == "openai/M3-highspeed"
    assert captured["kwargs"]["api_key"] == "sk-test"
    assert captured["kwargs"]["api_base"] == "https://api.minimaxi.com/v1"
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_minimax_401_raises_keyinvalid_no_retry(monkeypatch):
    class _AuthErr(Exception):
        pass

    call_count = {"n": 0}

    async def fake_acompletion(*args: Any, **kwargs: Any):
        call_count["n"] += 1
        raise _AuthErr("status 401 unauthorized: invalid api key")

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    d = MiniMaxDistiller(api_key="sk-dead", max_retries=3)
    with pytest.raises(DistillerKeyInvalid):
        await d.distill(SAMPLE_RAW)
    # No retries on auth errors — the provider already said "no".
    assert call_count["n"] == 1
