"""Anthropic distiller — uses litellm to call claude-3-5-sonnet (or any
Anthropic Claude model).

Configuration:
- Reads ``ANTHROPIC_API_KEY`` from the env (Tauri injects it from the
  user's keychain on sidecar start).
- Model defaults to ``anthropic/claude-3-5-sonnet-20241022`` but can be
  overridden in active_provider.json.

Inherits retry / 401 / rate-limit / JSON parsing from
:class:`LitellmDistiller`.
"""

from __future__ import annotations

from prism_sidecar.distillers.base import LitellmDistiller


class AnthropicDistiller(LitellmDistiller):
    provider_name = "anthropic"
    default_model = "anthropic/claude-3-5-sonnet-20241022"
    env_key_var = "ANTHROPIC_API_KEY"


__all__ = ["AnthropicDistiller"]
