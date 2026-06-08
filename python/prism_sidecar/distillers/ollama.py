"""Ollama distiller — uses litellm to call a locally running Ollama server.

Ollama is the one provider that has *no* API key. The base class
``LitellmDistiller`` supports this: when ``env_key_var = None`` the
distiller never raises ``DistillerNotConfigured`` and never passes an
``api_key`` kwarg to litellm.

Configuration:
- No API key.
- Reads ``api_base`` from the env (``OLLAMA_API_BASE``, defaults to
  ``http://127.0.0.1:11434``) so a user with a remote Ollama can point
  at it. The base URL is forwarded to litellm as ``api_base``.
- Model defaults to ``ollama/qwen2.5:7b``; can be overridden.

The model string in litellm is ``ollama/<model>``.
"""

from __future__ import annotations

import os
from typing import Any

from prism_sidecar.distillers.base import LitellmDistiller


class OllamaDistiller(LitellmDistiller):
    provider_name = "ollama"
    default_model = "ollama/qwen2.5:7b"
    env_key_var = None  # keyless

    def __init__(
        self,
        api_key: str | None = None,  # accepted for symmetry, ignored
        model: str | None = None,
        api_base: str | None = None,
        **extra: Any,
    ) -> None:
        # Resolve api_base: explicit > env > default.
        self._api_base = (
            api_base
            or os.environ.get("OLLAMA_API_BASE")
            or "http://127.0.0.1:11434"
        )
        super().__init__(api_key=None, model=model, **extra)

    def _extra_litellm_kwargs(self) -> dict[str, Any]:
        return {"api_base": self._api_base}


__all__ = ["OllamaDistiller"]
