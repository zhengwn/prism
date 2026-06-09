"""MiniMax (M3) distiller — OpenAI-compatible endpoint at api.minimaxi.com.

Configuration:
- Reads ``MINIMAX_API_KEY`` from the env (Tauri is expected to inject it
  on sidecar start, based on the user's keychain entry).
- Uses the ``openai/`` litellm model prefix so the request is routed
  through litellm's OpenAI-compatible adapter against MiniMax's HTTP
  endpoint. The model NAME is what the user picked (defaults to M3).
- ``api_base`` defaults to ``https://api.minimaxi.com/v1``; can be
  overridden in ``active_provider.json`` for a private deployment or a
  mirror.

v0.2a+ (post provider pruning): the only two supported providers are
DeepSeek and MiniMax. MiniMax uses the OpenAI-compatible protocol; the
Custom provider is gone in favour of this hard-coded entry.
"""

from __future__ import annotations

import os
from typing import Any

from prism_sidecar.distillers.base import LitellmDistiller


class MiniMaxDistiller(LitellmDistiller):
    provider_name = "minimax"
    default_model = "openai/MiniMax-M3"  # OpenAI-compat prefix; sent to api.minimaxi.com/v1
    env_key_var = "MINIMAX_API_KEY"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_base: str | None = None,
        **extra: Any,
    ) -> None:
        # Resolve api_base: explicit > env > default. The env var
        # follows the same pattern as DeepSeek / Ollama so power users
        # can re-point the sidecar at a mirror without touching the
        # keychain.
        self._api_base = (
            api_base
            or os.environ.get("MINIMAX_API_BASE")
            or "https://api.minimaxi.com/v1"
        )
        # litellm needs the openai/ prefix on the model string so the
        # request routes through the OpenAI-compatible adapter. If the
        # caller already typed the prefix, leave it alone.
        if model:
            if not model.startswith("openai/"):
                litellm_model = f"openai/{model}"
            else:
                litellm_model = model
        else:
            litellm_model = self.default_model
        super().__init__(api_key=api_key, model=litellm_model, **extra)

    def _extra_litellm_kwargs(self) -> dict[str, Any]:
        return {"api_base": self._api_base}


__all__ = ["MiniMaxDistiller"]
