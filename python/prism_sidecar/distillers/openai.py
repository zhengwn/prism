"""OpenAI distiller — uses litellm to call gpt-4o-mini (or any OpenAI model).

Configuration:
- Reads ``OPENAI_API_KEY`` from the env (Tauri injects it on sidecar
  start, based on the user's keychain entry).
- Model defaults to ``openai/gpt-4o-mini`` but can be overridden in
  active_provider.json.

Inherits the litellm retry / 401 / rate-limit / JSON parsing machinery
from :class:`LitellmDistiller`.
"""

from __future__ import annotations

from prism_sidecar.distillers.base import LitellmDistiller


class OpenAIDistiller(LitellmDistiller):
    provider_name = "openai"
    default_model = "openai/gpt-4o-mini"
    env_key_var = "OPENAI_API_KEY"


__all__ = ["OpenAIDistiller"]
