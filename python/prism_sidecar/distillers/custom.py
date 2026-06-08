"""Custom distiller — any OpenAI-compatible HTTP endpoint.

The user supplies ``base_url`` + ``model`` + ``api_key``. We forward
``api_base`` to litellm and use the ``openai/<model>`` model string,
which is litellm's idiomatic way to call a generic OpenAI-compatible
endpoint. This covers MiniMax / 智谱 / Moonshot / LM Studio / vLLM / etc.

Configuration:
- Reads ``api_key`` from the env (``OPENAI_API_KEY`` — Tauri injects
  the user's "custom" keychain entry into this variable for parity
  with the regular OpenAI provider).
- ``api_base`` is required (set by the registry from
  ``active_provider.json``).
- ``model`` defaults to empty (the user MUST supply one in
  active_provider.json).
"""

from __future__ import annotations

from typing import Any

from prism_sidecar.distillers.base import LitellmDistiller


class CustomDistiller(LitellmDistiller):
    provider_name = "custom"
    default_model = ""  # registry must inject the actual model name
    env_key_var = "OPENAI_API_KEY"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_base: str | None = None,
        **extra: Any,
    ) -> None:
        if not api_base:
            raise ValueError(
                "CustomDistiller requires a non-empty api_base "
                "(the OpenAI-compatible endpoint URL)."
            )
        # litellm expects the openai/ prefix on the model string even
        # when the endpoint isn't literally OpenAI. The model NAME is
        # what the user gave us; we wrap it.
        if model:
            if not model.startswith("openai/"):
                litellm_model = f"openai/{model}"
            else:
                litellm_model = model
        else:
            litellm_model = "openai/custom"
        # Use a sentinel on _model before super().__init__ so the
        # base class picks it up.
        super().__init__(api_key=api_key, model=litellm_model, **extra)
        self._api_base = api_base

    def _extra_litellm_kwargs(self) -> dict[str, Any]:
        return {"api_base": self._api_base}


__all__ = ["CustomDistiller"]
