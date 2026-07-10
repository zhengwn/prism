"""Sidecar-side settings: which LLM provider is active.

The active provider config lives in a small JSON file under the
sidecar's data dir (``active_provider.json``). It is **read once at
startup** and **rewritten** by ``POST /api/settings/llm``. The file
**never** contains the API key — keys come from the process env, which
Tauri populates from the OS keychain before spawning the sidecar.

Schema::

    {
      "provider": "deepseek" | "minimax",
      "model":    "<override; optional, falls back to default_model>",
      "base_url": "<required for minimax; otherwise unused>"
    }

Public API:

* :func:`load_active_provider` — read the file (or default)
* :func:`set_active_provider` — overwrite the file
* :func:`is_provider_configured` — does the env have the right key?
* :data:`PROVIDER_SCHEMAS` — declarative metadata for the Settings UI
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from prism_sidecar.config import PRISM_DATA_DIR

log = logging.getLogger(__name__)


# ----- Public path --------------------------------------------------------

ACTIVE_PROVIDER_PATH: Path = PRISM_DATA_DIR / "active_provider.json"

DEFAULT_PROVIDER_ID = "deepseek"


# ----- Pydantic models (the API surface) ---------------------------------


class _CamelBase(BaseModel):
    """Auto-generate camelCase JSON aliases for snake_case fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class ProviderField(_CamelBase):
    """A single input field shown in the Settings UI for a provider."""

    name: str
    label: str
    required: bool
    default: Optional[str] = None
    placeholder: Optional[str] = None


class ProviderSchema(_CamelBase):
    """Declarative description of one provider's Settings UI shape."""

    id: str
    label: str
    hint: str
    requires_key: bool
    default_model: str
    fields: list[ProviderField]


class LlmConfig(_CamelBase):
    """The current active LLM configuration (as seen by the UI).

    Returned by ``GET /api/settings/llm`` and ``POST /api/settings/llm``.
    Note: ``api_key`` is never included.
    """

    provider: str
    configured: bool
    model: Optional[str] = None
    base_url: Optional[str] = None


class LlmConfigUpdate(_CamelBase):
    """Body of ``POST /api/settings/llm``.

    The body MUST NOT include ``api_key`` — Tauri writes the key to
    the OS keychain and (re)launches the sidecar with the right env
    vars. ``api_key`` is declared on this model so we can detect /
    reject it (returns 400); the field is excluded from the
    serialized form so it can never echo back through the response.
    """

    model_config = ConfigDict(extra="ignore")  # api_key accepted, then rejected

    provider: str
    api_key: Optional[str] = Field(default=None, exclude=True)
    model: Optional[str] = None
    base_url: Optional[str] = None


# ----- Static schema (the source of truth for the UI) ---------------------


PROVIDER_SCHEMAS: list[ProviderSchema] = [
    ProviderSchema(
        id="deepseek",
        label="DeepSeek",
        hint="中文最强，便宜",
        requires_key=True,
        default_model="deepseek-v4-pro",  # user-facing id; litellm prefix added inside the distiller
        fields=[
            ProviderField(
                name="api_key", label="apiKey", required=True,
                placeholder="sk-...",
            )
        ],
    ),
    ProviderSchema(
        id="minimax",
        label="MiniMax",
        hint="M3，百万上下文，OpenAI 兼容",
        requires_key=True,
        default_model="MiniMax-M3",  # user-facing id; litellm "openai/" prefix added inside the distiller
        fields=[
            ProviderField(
                name="api_key", label="apiKey", required=True,
                placeholder="ey...",
            )
        ],
    ),
]


# Env var name per provider (None = keyless).
_PROVIDER_ENV_KEY: dict[str, Optional[str]] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


# ----- Read / write -------------------------------------------------------


def _empty_default() -> dict[str, Any]:
    # Tauri injects PRISM_ACTIVE_PROVIDER when spawning the sidecar;
    # honour it as the default when the marker file is missing or
    # corrupt. (sidecar.rs always documented this fallback, but the
    # sidecar never actually read the var before v0.2c.)
    env_provider = os.environ.get("PRISM_ACTIVE_PROVIDER") or ""
    if env_provider in {s.id for s in PROVIDER_SCHEMAS}:
        return {"provider": env_provider}
    return {"provider": DEFAULT_PROVIDER_ID}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via tmp-file + rename so a concurrent reader (the
    Tauri shell also writes/reads this file around sidecar restarts)
    never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_active_provider() -> dict[str, Any]:
    """Return the on-disk active provider config, or the default.

    Never raises: a corrupt or missing file is logged and the default
    is returned. The default file is *not* automatically written
    here — the caller (lifespan) does that to keep the read path
    pure.
    """
    if not ACTIVE_PROVIDER_PATH.exists():
        return _empty_default()
    try:
        with ACTIVE_PROVIDER_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "[settings] failed to read %s: %s — falling back to default",
            ACTIVE_PROVIDER_PATH, exc,
        )
        return _empty_default()
    # Sanity-check the provider id.
    provider = data.get("provider") or DEFAULT_PROVIDER_ID
    if provider not in {s.id for s in PROVIDER_SCHEMAS}:
        log.warning(
            "[settings] unknown provider %r in %s — falling back to default",
            provider, ACTIVE_PROVIDER_PATH,
        )
        return _empty_default()
    data = dict(data)
    data["provider"] = provider
    return data


def write_default_if_missing() -> dict[str, Any]:
    """Create the default file if it doesn't exist yet.

    Returns the (now-on-disk) active provider config.
    """
    if not ACTIVE_PROVIDER_PATH.exists():
        try:
            _atomic_write_json(ACTIVE_PROVIDER_PATH, _empty_default())
            log.info(
                "[settings] wrote default active provider to %s",
                ACTIVE_PROVIDER_PATH,
            )
        except OSError as exc:
            log.warning("[settings] could not write default: %s", exc)
    return load_active_provider()


def set_active_provider(
    provider: str,
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """Overwrite the active provider file with the new values.

    The key is **not** part of this function — Tauri handles that
    separately by writing to the OS keychain and (re)launching the
    sidecar with the right env vars.

    Returns the new on-disk config (so the API endpoint can echo it).
    """
    if provider not in {s.id for s in PROVIDER_SCHEMAS}:
        raise ValueError(f"unknown provider: {provider!r}")
    payload: dict[str, Any] = {"provider": provider}
    if model:
        payload["model"] = model
    if base_url:
        payload["base_url"] = base_url
    _atomic_write_json(ACTIVE_PROVIDER_PATH, payload)
    log.info(
        "[settings] active provider updated: %s (model=%s, base_url=%s)",
        provider, model, base_url,
    )
    return load_active_provider()


# ----- Status helpers -----------------------------------------------------


def is_provider_configured(provider: str) -> bool:
    """True if the provider's required env var is set & non-empty.

    For keyless providers this is always True (none currently, but
    the branch is preserved for parity with the base class).
    """
    env_var = _PROVIDER_ENV_KEY.get(provider)
    if env_var is None:
        return True
    val = os.environ.get(env_var) or ""
    return bool(val)


def get_llm_status() -> LlmConfig:
    """Build the LlmConfig returned by ``GET /api/settings/llm``."""
    cfg = load_active_provider()
    provider = cfg["provider"]
    return LlmConfig(
        provider=provider,
        configured=is_provider_configured(provider),
        model=cfg.get("model"),
        base_url=cfg.get("base_url"),
    )


__all__ = [
    "ACTIVE_PROVIDER_PATH",
    "DEFAULT_PROVIDER_ID",
    "PROVIDER_SCHEMAS",
    "ProviderField",
    "ProviderSchema",
    "LlmConfig",
    "LlmConfigUpdate",
    "load_active_provider",
    "write_default_if_missing",
    "set_active_provider",
    "is_provider_configured",
    "get_llm_status",
]
