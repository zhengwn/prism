"""Runtime configuration for the Prism sidecar.

Reads from environment variables. The Tauri shell is expected to write the
DeepSeek API key (and any other secrets) into the process environment before
spawning the sidecar; we do NOT manage the OS keychain ourselves.
"""

from __future__ import annotations

import os
from pathlib import Path


# ----- LLM ----------------------------------------------------------------

DEEPSEEK_API_KEY: str | None = os.environ.get("DEEPSEEK_API_KEY") or None

DEEPSEEK_MODEL: str = os.environ.get("PRISM_DEEPSEEK_MODEL", "deepseek/deepseek-chat")

# When True and litellm is missing a key, log and skip distillation rather
# than crashing the whole sync. Useful for first-run / dev.
DISTILLER_OPTIONAL: bool = (
    os.environ.get("PRISM_DISTILLER_OPTIONAL", "1").lower() not in {"0", "false", "no"}
)


# ----- Storage ------------------------------------------------------------

PRISM_DATA_DIR: Path = Path(
    os.environ.get("PRISM_DATA_DIR", str(Path.home() / ".prism"))
)

PRISM_DB_PATH: Path = PRISM_DATA_DIR / "data.db"


# ----- Scheduler ----------------------------------------------------------

DAILY_SYNC_HOUR: int = int(os.environ.get("PRISM_DAILY_SYNC_HOUR", "9"))

DAILY_SYNC_TZ: str = os.environ.get("PRISM_DAILY_SYNC_TZ", "Asia/Shanghai")

# Set to "0" / "false" to disable the daily cron entirely (tests, dev).
DAILY_SYNC_ENABLED: bool = os.environ.get("PRISM_DAILY_SYNC_DISABLED", "0").lower() in {
    "0",
    "false",
    "no",
    "",
}


# ----- Network ------------------------------------------------------------

FETCH_TIMEOUT_SEC: float = float(os.environ.get("PRISM_FETCH_TIMEOUT_SEC", "15"))

FETCH_MAX_RETRIES: int = int(os.environ.get("PRISM_FETCH_MAX_RETRIES", "2"))

FETCH_RETRY_BACKOFF_SEC: float = float(os.environ.get("PRISM_FETCH_RETRY_BACKOFF_SEC", "1.0"))

# Sleep between sources to be polite to upstream RSS servers.
FETCH_INTER_SOURCE_SLEEP_SEC: float = float(
    os.environ.get("PRISM_FETCH_INTER_SOURCE_SLEEP_SEC", "1.0")
)

# Window of items to keep from a single fetch (cutoff = now - window).
FETCH_LOOKBACK_DAYS: int = int(os.environ.get("PRISM_FETCH_LOOKBACK_DAYS", "7"))

# Wider lookback for the very first sync per source, so a fresh install
# gets a meaningful chunk of history instead of a sparse 7-day slice.
# Low-frequency sources (e.g. DeepMind Blog) often have nothing in the
# last 7 days, which makes a new install look broken. After the first
# successful sync of a source, it falls back to FETCH_LOOKBACK_DAYS.
INITIAL_FETCH_LOOKBACK_DAYS: int = int(
    os.environ.get("PRISM_INITIAL_FETCH_LOOKBACK_DAYS", "30")
)


def is_distiller_configured() -> bool:
    """True if the DeepSeek distiller has an API key to work with."""
    return DEEPSEEK_API_KEY is not None and len(DEEPSEEK_API_KEY) > 0


__all__ = [
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "DISTILLER_OPTIONAL",
    "PRISM_DATA_DIR",
    "PRISM_DB_PATH",
    "DAILY_SYNC_HOUR",
    "DAILY_SYNC_TZ",
    "DAILY_SYNC_ENABLED",
    "FETCH_TIMEOUT_SEC",
    "FETCH_MAX_RETRIES",
    "FETCH_RETRY_BACKOFF_SEC",
    "FETCH_INTER_SOURCE_SLEEP_SEC",
    "FETCH_LOOKBACK_DAYS",
    "INITIAL_FETCH_LOOKBACK_DAYS",
    "is_distiller_configured",
]
