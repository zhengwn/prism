"""MiniMax (M3) distiller — OpenAI-compatible endpoint at api.minimaxi.com.

Configuration:
- Reads ``MINIMAX_API_KEY`` from the env (Tauri is expected to inject it
  on sidecar start, based on the user's keychain entry).
- Uses the ``openai/`` litellm model prefix so the request is routed
  through litellm's OpenAI-compatible adapter against MiniMax's HTTP
  endpoint. The user-facing model NAME is what the user picks
  (defaults to ``M3``); the distiller prepends ``openai/`` internally.
- ``api_base`` defaults to ``https://api.minimaxi.com/v1``; can be
  overridden by the active-provider marker or the ``MINIMAX_API_BASE``
  env var for a private deployment or a mirror.

v0.2a+ (post provider pruning): the only two supported providers are
DeepSeek and MiniMax. MiniMax uses the OpenAI-compatible protocol; the
Custom provider is gone in favour of this hard-coded entry.

Model string convention
-----------------------
``default_model`` and the per-user override carry the **user-facing**
model id (e.g. ``"M3"`` or ``"M3-highspeed"``). The ``__init__`` strips
any redundant ``openai/`` prefix and re-adds the canonical one before
calling ``litellm.acompletion``. The user never sees or types the
``openai/`` prefix.
"""

from __future__ import annotations

import os
from typing import Any

from prism_sidecar.distillers.base import LitellmDistiller


class MiniMaxDistiller(LitellmDistiller):
    provider_name = "minimax"
    default_model = "MiniMax-M3"  # user-facing; litellm prefix added by base
    env_key_var = "MINIMAX_API_KEY"
    # litellm prefix for routing — kept on the class so tests can pin it.
    _LITELLM_PREFIX = "openai/"

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
        # The caller (or default_model) gives us the user-facing id;
        # strip any redundant prefix and re-add the canonical one.
        bare = (model or self.default_model).removeprefix(self._LITELLM_PREFIX)
        litellm_model = f"{self._LITELLM_PREFIX}{bare}"
        super().__init__(api_key=api_key, model=litellm_model, **extra)

    def _extra_litellm_kwargs(self) -> dict[str, Any]:
        return {"api_base": self._api_base}


__all__ = ["MiniMaxDistiller"]
