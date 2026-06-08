"""Test the distiller registry: provider id → distiller class.

Also covers the deepseek (back-compat) and the unknown-provider error
path that pipeline code relies on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.anthropic import AnthropicDistiller
from prism_sidecar.distillers.base import LitellmDistiller
from prism_sidecar.distillers.custom import CustomDistiller
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.distillers.ollama import OllamaDistiller
from prism_sidecar.distillers.openai import OpenAIDistiller
from prism_sidecar.distillers.registry import PROVIDERS, get_distiller
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ContentType


SAMPLE_RAW = RawItem(
    url="https://example.com/p",
    title="T",
    content="C",
    published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    content_type=ContentType.article,
)


# ---- registry: each provider id resolves to the right class -------------


def test_registry_has_5_providers():
    assert set(PROVIDERS.keys()) == {"deepseek", "openai", "anthropic", "ollama", "custom"}


def test_get_distiller_deepseek_default():
    d = get_distiller("deepseek")
    assert isinstance(d, DeepSeekDistiller)
    # Model string should still carry the deepseek/ prefix.
    assert d._model == "deepseek/deepseek-chat"


def test_get_distiller_openai_default():
    d = get_distiller("openai")
    assert isinstance(d, OpenAIDistiller)
    assert d._model == "openai/gpt-4o-mini"


def test_get_distiller_openai_with_explicit_model():
    d = get_distiller("openai", model="openai/gpt-4o")
    assert d._model == "openai/gpt-4o"


def test_get_distiller_anthropic_default():
    d = get_distiller("anthropic")
    assert isinstance(d, AnthropicDistiller)
    assert d._model == "anthropic/claude-3-5-sonnet-20241022"


def test_get_distiller_ollama_default():
    d = get_distiller("ollama")
    assert isinstance(d, OllamaDistiller)
    # Ollama default model is wrapped in the ollama/ prefix.
    assert d._model == "ollama/qwen2.5:7b"
    # No api_key is read for Ollama — keyless provider.
    assert d._api_key is None


def test_get_distiller_ollama_with_explicit_base_url():
    d = get_distiller("ollama", base_url="http://remote:11434")
    assert d._api_base == "http://remote:11434"
    assert d._model == "ollama/qwen2.5:7b"


def test_get_distiller_custom_requires_base_url():
    d = get_distiller("custom", model="my-model", base_url="https://api.x/v1")
    assert isinstance(d, CustomDistiller)
    # Custom wraps the user-supplied model in the openai/ prefix so
    # litellm knows to use the OpenAI-compatible HTTP path.
    assert d._model == "openai/my-model"
    assert d._api_base == "https://api.x/v1"


def test_get_distiller_custom_preserves_already_prefixed_model():
    d = get_distiller("custom", model="openai/zz", base_url="https://api.x/v1")
    assert d._model == "openai/zz"


def test_get_distiller_custom_rejects_missing_base_url():
    """Custom must have a base_url — without it we can't route the call."""
    with pytest.raises(ValueError):
        get_distiller("custom", model="m")


def test_get_distiller_unknown_provider_raises():
    with pytest.raises(ValueError) as exc:
        get_distiller("llamastack")
    assert "unknown provider" in str(exc.value).lower()


# ---- All 5 distillers are LitellmDistiller subclasses -------------------


@pytest.mark.parametrize("provider_id", list(PROVIDERS.keys()))
def test_every_provider_is_litellm_subclass(provider_id: str):
    d = get_distiller(provider_id, base_url="http://x" if provider_id in {"ollama", "custom"} else None)
    assert isinstance(d, LitellmDistiller)
    # Every provider has a non-empty provider_name for log messages.
    assert d.provider_name
    assert d._model  # non-empty


# ---- Custom accepts a custom-model name and a custom key ----------------


def test_custom_with_api_key(monkeypatch):
    """The Custom provider reads OPENAI_API_KEY (Tauri's env injection)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-custom")
    d = get_distiller("custom", model="my-model", base_url="https://api.x/v1")
    assert d._api_key == "sk-custom"


# ---- end-to-end with mocked litellm: each provider's model string is right


@pytest.mark.parametrize(
    "provider_id, expected_model_prefix",
    [
        ("deepseek", "deepseek/"),
        ("openai", "openai/"),
        ("anthropic", "anthropic/"),
        ("ollama", "ollama/"),
    ],
)
@pytest.mark.asyncio
async def test_provider_passes_correct_model_string(
    monkeypatch, provider_id: str, expected_model_prefix: str,
):
    captured: dict[str, Any] = {}

    async def fake_acompletion(*args: Any, **kwargs: Any):
        captured["model"] = kwargs.get("model")
        # Return a valid minimal response so the parser succeeds.
        return {
            "choices": [
                {"message": {"content": '{"title_zh":"x","summary_zh":"y","key_points_zh":[],"tags_zh":[]}'}}
            ]
        }

    fake_litellm = type("L", (), {"acompletion": staticmethod(fake_acompletion)})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    # Each provider's key is in a different env var.
    env = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "ollama": None,  # keyless
    }[provider_id]
    if env:
        monkeypatch.setenv(env, f"sk-{provider_id}")

    kwargs: dict[str, Any] = {}
    if provider_id == "ollama":
        kwargs["base_url"] = "http://127.0.0.1:11434"
    if provider_id == "custom":
        kwargs.update(model="zz", base_url="https://api.x/v1")

    d = get_distiller(provider_id, **kwargs)
    await d.distill(SAMPLE_RAW)
    assert captured["model"].startswith(expected_model_prefix), (
        f"{provider_id} → model={captured['model']!r}"
    )
