"""Test the distiller registry: provider id → distiller class.

Also covers the unknown-provider error path that pipeline code relies on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from prism_sidecar.distillers.base import LitellmDistiller
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.distillers.minimax import MiniMaxDistiller
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


def test_registry_has_2_providers():
    assert set(PROVIDERS.keys()) == {"deepseek", "minimax"}


def test_get_distiller_deepseek_default():
    d = get_distiller("deepseek")
    assert isinstance(d, DeepSeekDistiller)
    # `default_model` is the user-facing id; the distiller prepends the
    # litellm "deepseek/" routing prefix in `__init__`.
    assert d.default_model == "deepseek-v4-pro"
    assert d._model == "deepseek/deepseek-v4-pro"


def test_get_distiller_minimax_default():
    d = get_distiller("minimax")
    assert isinstance(d, MiniMaxDistiller)
    # `default_model` is the user-facing id; the distiller prepends the
    # litellm "openai/" routing prefix in `__init__`.
    assert d.default_model == "MiniMax-M3"
    assert d._model == "openai/MiniMax-M3"
    # Base URL defaults to the public MiniMax endpoint.
    assert d._api_base == "https://api.minimaxi.com/v1"


def test_get_distiller_minimax_with_explicit_model():
    d = get_distiller("minimax", model="M3-highspeed")
    assert d._model == "openai/M3-highspeed"


def test_get_distiller_minimax_with_explicit_base_url():
    d = get_distiller("minimax", base_url="https://mirror.example/v1")
    assert d._api_base == "https://mirror.example/v1"


def test_get_distiller_minimax_strips_redundant_litellm_prefix():
    d = get_distiller("minimax", model="openai/M3-elsewhere")
    # Stale configs might hand us a model with the openai/ prefix
    # already; the distiller must not double-prefix.
    assert d._model == "openai/M3-elsewhere"


def test_get_distiller_unknown_provider_raises():
    with pytest.raises(ValueError) as exc:
        get_distiller("llamastack")
    assert "unknown provider" in str(exc.value).lower()


# ---- All distillers are LitellmDistiller subclasses ----------------------


@pytest.mark.parametrize("provider_id", list(PROVIDERS.keys()))
def test_every_provider_is_litellm_subclass(provider_id: str):
    d = get_distiller(provider_id)
    assert isinstance(d, LitellmDistiller)
    # Every provider has a non-empty provider_name for log messages.
    assert d.provider_name
    assert d._model  # non-empty


# ---- end-to-end with mocked litellm: each provider's model string is right


@pytest.mark.parametrize(
    "provider_id, expected_model_prefix",
    [
        ("deepseek", "deepseek/"),
        ("minimax", "openai/"),
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
        "minimax": "MINIMAX_API_KEY",
    }[provider_id]
    monkeypatch.setenv(env, f"sk-{provider_id}")

    d = get_distiller(provider_id)
    await d.distill(SAMPLE_RAW)
    assert captured["model"].startswith(expected_model_prefix), (
        f"{provider_id} → model={captured['model']!r}"
    )
