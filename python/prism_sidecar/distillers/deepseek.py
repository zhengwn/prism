"""DeepSeek distiller — uses litellm to call deepseek-chat.

Why litellm: it gives us one async call interface for OpenAI / Anthropic /
DeepSeek / etc. We can swap to a local model later by changing the model
string.

Configuration:
- Reads ``DEEPSEEK_API_KEY`` from the env (Tauri is expected to inject it).
- Rate limit: at most 1 request per second (asyncio.Semaphore + delay).
- Retry: 2 attempts with exponential backoff on transient errors.
- 401/403/quota errors raise DistillerKeyInvalid immediately (no retry)
  so we don't burn what little credit a dying key has left.
- On final failure, raises so the pipeline can mark the item as
  ``distilled_at=NULL`` and move on.
"""

from __future__ import annotations

from prism_sidecar.distillers.base import LitellmDistiller


class DeepSeekDistiller(LitellmDistiller):
    provider_name = "deepseek"
    default_model = "deepseek/deepseek-chat"
    env_key_var = "DEEPSEEK_API_KEY"


__all__ = ["DeepSeekDistiller"]
