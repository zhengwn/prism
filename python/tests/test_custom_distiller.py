"""Test the Custom (OpenAI-compatible) distiller."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.base import DistillerKeyInvalid
from prism_sidecar.distillers.custom import CustomDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


SAMPLE_RAW = RawItem(
    url="https://example.com/p",
    title="Custom endpoint",
    content="Some custom provider.",
    published_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    author="Custom",
    content_type=ContentType.article,
)


SAMPLE_GOOD = {
    "title_zh": "自定义端点",
    "summary_zh": "OpenAI 兼容接口",
    "key_points_zh": ["兼容", "灵活"],
    "tags_zh": ["custom"],
}


def test_custom_requires_base_url():
    with pytest.raises(ValueError):
        CustomDistiller(api_key="sk-x", model="m")


def test_custom_wraps_model_in_openai_prefix():
    """The user passes a bare model name; we wrap it for litellm."""
    d = CustomDistiller(api_key="sk-x", model="my-model", api_base="https://api.x/v1")
    assert d._model == "openai/my-model"


def test_custom_preserves_existing_openai_prefix():
    d = CustomDistiller(api_key="sk-x", model="openai/zz", api_base="https://api.x/v1")
    assert d._model == "openai/zz"


def test_custom_default_model_falls_back_to_openai_custom():
    d = CustomDistiller(api_key="sk-x", model=None, api_base="https://api.x/v1")
    # No model → sentinel "openai/custom" (will likely 400 server-side,
    # but the constructor should not blow up).
    assert d._model == "openai/custom"


def test_custom_passes_api_base_through(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-custom")
    d = CustomDistiller(model="m", api_base="https://api.example.com/v1")
    assert d._api_key == "sk-custom"
    assert d._api_base == "https://api.example.com/v1"


@pytest.mark.asyncio
async def test_custom_calls_litellm_with_openai_prefix_and_api_base(monkeypatch):
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

    d = CustomDistiller(
        api_key="sk-custom", model="my-model", api_base="https://api.example.com/v1",
        max_retries=0,
    )
    out = await d.distill(SAMPLE_RAW)

    assert out.title_zh == "自定义端点"
    assert captured["kwargs"]["model"] == "openai/my-model"
    assert captured["kwargs"]["api_key"] == "sk-custom"
    assert captured["kwargs"]["api_base"] == "https://api.example.com/v1"
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_custom_401_raises_keyinvalid(monkeypatch):
    class _AuthErr(Exception):
        pass

    async def fake_acompletion(*args: Any, **kwargs: Any):
        raise _AuthErr("status 401 unauthorized: invalid api key")

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    d = CustomDistiller(
        api_key="sk-dead", model="m", api_base="https://api.x/v1", max_retries=2,
    )
    with pytest.raises(DistillerKeyInvalid):
        await d.distill(SAMPLE_RAW)
