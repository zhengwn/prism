"""Test the Ollama distiller (keyless, base URL driven)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.ollama import OllamaDistiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


SAMPLE_RAW = RawItem(
    url="https://example.com/p",
    title="Ollama local",
    content="Qwen 2.5 7B running locally.",
    published_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    author="Ollama",
    content_type=ContentType.article,
)


SAMPLE_GOOD = {
    "title_zh": "Ollama 本地 Qwen 2.5",
    "summary_zh": "本地 7B 模型",
    "key_points_zh": ["本地", "便宜"],
    "tags_zh": ["ollama", "qwen"],
}


def test_ollama_does_not_require_api_key():
    """Ollama is keyless — constructor accepts None without error."""
    d = OllamaDistiller()
    assert d._api_key is None


def test_ollama_default_model_string():
    d = OllamaDistiller()
    assert d._model == "ollama/qwen2.5:7b"


def test_ollama_default_base_url(monkeypatch):
    """No env, no kwarg → default localhost:11434."""
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    d = OllamaDistiller()
    assert d._api_base == "http://127.0.0.1:11434"


def test_ollama_reads_base_url_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://192.168.1.5:11434")
    d = OllamaDistiller()
    assert d._api_base == "http://192.168.1.5:11434"


def test_ollama_explicit_base_url_wins():
    d = OllamaDistiller(api_base="http://remote:9999")
    assert d._api_base == "http://remote:9999"


def test_ollama_explicit_model():
    d = OllamaDistiller(model="ollama/llama3.1:8b")
    assert d._model == "ollama/llama3.1:8b"


@pytest.mark.asyncio
async def test_ollama_calls_litellm_with_api_base_and_no_key(monkeypatch):
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

    d = OllamaDistiller(api_base="http://127.0.0.1:11434", max_retries=0)
    out = await d.distill(SAMPLE_RAW)

    assert out.title_zh == "Ollama 本地 Qwen 2.5"
    assert captured["kwargs"]["model"] == "ollama/qwen2.5:7b"
    # The base class must NOT pass api_key for keyless providers.
    assert "api_key" not in captured["kwargs"]
    assert captured["kwargs"]["api_base"] == "http://127.0.0.1:11434"


def test_ollama_passing_api_key_is_ignored():
    """Defensive: even if a user passes api_key by accident, we drop it."""
    d = OllamaDistiller(api_key="sk-ignored")
    assert d._api_key is None
