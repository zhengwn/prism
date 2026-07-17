"""Health snapshot for GET /health."""

from __future__ import annotations

from datetime import datetime, timezone

from prism_sidecar.db import get_db

_started_at: datetime = datetime.now(timezone.utc)


async def health_snapshot() -> dict:
    import prism_sidecar.config as _cfg
    from prism_sidecar import __version__
    from prism_sidecar import settings as _settings
    PRISM_DB_PATH = _cfg.PRISM_DB_PATH

    db = get_db()
    cur = await db.execute("SELECT COUNT(*) FROM sources")
    sources_count = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM items")
    items_count = (await cur.fetchone())[0]
    # v0.2a+: check the *active* provider (DeepSeek or MiniMax) rather
    # than the v0.1 DeepSeek-only helper.
    active = _settings.load_active_provider()
    return {
        "ok": True,
        "version": __version__,
        "sources_count": int(sources_count),
        "items_count": int(items_count),
        "distiller_configured": _settings.is_provider_configured(active["provider"]),
        "db_path": str(PRISM_DB_PATH),
        "uptime_sec": int((datetime.now(timezone.utc) - _started_at).total_seconds()),
    }
