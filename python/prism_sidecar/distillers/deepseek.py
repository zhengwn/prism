"""DeepSeek distiller — uses litellm to call deepseek-v4-pro.

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

Model string convention
-----------------------
``default_model`` and the per-user override (via ``active_provider.json``)
carry the **user-facing** model id (e.g. ``"deepseek-v4-pro"``). This
class prepends the litellm routing prefix ``"deepseek/"`` before calling
``litellm.acompletion`` so litellm knows which adapter to dispatch to.
The user never sees or types the ``deepseek/`` prefix.
"""

from __future__ import annotations

from prism_sidecar.distillers.base import LitellmDistiller


class DeepSeekDistiller(LitellmDistiller):
    provider_name = "deepseek"
    default_model = "deepseek-v4-pro"  # user-facing; litellm prefix added by base
    env_key_var = "DEEPSEEK_API_KEY"
    # litellm prefix for routing — kept on the class so tests can pin it.
    _LITELLM_PREFIX = "deepseek/"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        **kwargs,
    ) -> None:
        # The caller (or default_model) gives us the user-facing id;
        # strip any redundant prefix and re-add the canonical one.
        bare = (model or self.default_model).removeprefix(self._LITELLM_PREFIX)
        super().__init__(api_key=api_key, model=f"{self._LITELLM_PREFIX}{bare}", **kwargs)


__all__ = ["DeepSeekDistiller"]
