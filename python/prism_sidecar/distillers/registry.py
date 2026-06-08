"""Distiller registry.

Given an active provider id (and optional model / base_url), construct
the matching :class:`LitellmDistiller` subclass. The pipeline calls
:func:`get_distiller` from a single place so adding a new provider
is a 2-line change.

Provider id → distiller class mapping lives in :data:`PROVIDERS`.
Each entry knows:

* the distiller class
* the default model string
* whether a key is required
* what env var holds the key (or ``None`` for keyless providers)
"""

from __future__ import annotations

from typing import Type

from prism_sidecar.distillers.anthropic import AnthropicDistiller
from prism_sidecar.distillers.base import Distiller, LitellmDistiller
from prism_sidecar.distillers.custom import CustomDistiller
from prism_sidecar.distillers.deepseek import DeepSeekDistiller
from prism_sidecar.distillers.ollama import OllamaDistiller
from prism_sidecar.distillers.openai import OpenAIDistiller


class _ProviderSpec:
    """Bundle of metadata + distiller class for one provider id."""

    __slots__ = ("id", "label", "requires_key", "default_model", "cls")

    def __init__(
        self,
        id: str,
        label: str,
        requires_key: bool,
        default_model: str,
        cls: Type[LitellmDistiller],
    ) -> None:
        self.id = id
        self.label = label
        self.requires_key = requires_key
        self.default_model = default_model
        self.cls = cls


PROVIDERS: dict[str, _ProviderSpec] = {
    "deepseek": _ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        requires_key=True,
        default_model=DeepSeekDistiller.default_model,
        cls=DeepSeekDistiller,
    ),
    "openai": _ProviderSpec(
        id="openai",
        label="OpenAI",
        requires_key=True,
        default_model=OpenAIDistiller.default_model,
        cls=OpenAIDistiller,
    ),
    "anthropic": _ProviderSpec(
        id="anthropic",
        label="Anthropic",
        requires_key=True,
        default_model=AnthropicDistiller.default_model,
        cls=AnthropicDistiller,
    ),
    "ollama": _ProviderSpec(
        id="ollama",
        label="Ollama (本地)",
        requires_key=False,
        default_model=OllamaDistiller.default_model,
        cls=OllamaDistiller,
    ),
    "custom": _ProviderSpec(
        id="custom",
        label="Custom (OpenAI-compatible)",
        requires_key=True,
        default_model=CustomDistiller.default_model,
        cls=CustomDistiller,
    ),
}


def get_distiller(
    provider: str,
    model: str | None = None,
    base_url: str | None = None,
) -> Distiller:
    """Construct a distiller for the given provider.

    ``model`` and ``base_url`` are optional overrides pulled from
    ``active_provider.json``. Provider-specific kwargs (e.g. Ollama's
    ``api_base``, Custom's ``api_base`` + ``OPENAI_API_KEY``) are
    handled inside each distiller's ``__init__``.

    Raises:
        ValueError: if ``provider`` is not one of the 5 known ids.
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(
            f"unknown provider: {provider!r}. "
            f"Valid: {sorted(PROVIDERS.keys())}"
        )
    cls = spec.cls
    # Different providers accept different kwargs in __init__; build
    # the kwarg dict defensively.
    kwargs: dict[str, object] = {}
    if model:
        kwargs["model"] = model
    if base_url:
        # Only Ollama and Custom use this; others ignore it.
        kwargs["api_base"] = base_url
    return cls(**kwargs)  # type: ignore[abstract]


__all__ = ["get_distiller", "PROVIDERS"]
