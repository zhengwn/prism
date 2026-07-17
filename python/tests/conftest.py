"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

# ----- Env defaults — must be set BEFORE app import ---------------------

_DEFAULT_ENV = {
    "PRISM_DATA_DIR": "/tmp/prism-pytest-default",
    "PRISM_DAILY_SYNC_DISABLED": "1",
}


@pytest_asyncio.fixture(autouse=True)
async def isolated_data_dir(monkeypatch, tmp_path: Path) -> AsyncIterator[Path]:
    """Force every test to use a fresh tmp data dir.

    The trick: prism_sidecar.db caches the aiosqlite connection in a
    module-level global. We MUST close any cached connection before the
    test starts (so a new tmp path is picked up), and again after.
    """
    # Close any cached connection from a previous test.
    from prism_sidecar import db as dbmod
    await dbmod.close_db()

    data_dir = tmp_path / "prism-data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Patch env so future reads see the tmp path.
    for k, v in _DEFAULT_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PRISM_DATA_DIR", str(data_dir))

    # Patch the module-level constants in case anything imported them
    # already. The store + db modules read from these at call time, but
    # we still want fast access.
    import prism_sidecar.config as cfg
    import prism_sidecar.db as dbmod2
    monkeypatch.setattr(cfg, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(cfg, "PRISM_DB_PATH", data_dir / "data.db")
    # db.py and store.py do `from prism_sidecar.config import ...` at
    # import time, so the binding lives on those modules too. Patch
    # those module attributes so the cached values match.
    monkeypatch.setattr(dbmod2, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod2, "PRISM_DB_PATH", data_dir / "data.db")
    monkeypatch.setattr(cfg, "DAILY_SYNC_ENABLED", False)

    # No DEEPSEEK key by default; individual tests can override.
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", None)
    monkeypatch.setattr(cfg, "is_distiller_configured", lambda: False)

    yield data_dir

    # Cleanup: shut down the shared aiosqlite connection, close this
    # loop's shared httpx client (prism_sidecar._http), and remove the
    # tmp dir so tests don't leak state.
    try:
        await dbmod.close_db()
    except Exception:
        pass
    try:
        from prism_sidecar import _http
        await _http.aclose_current()
    except Exception:
        pass
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fast_host_throttle():
    """Zero out the per-host throttle so fetcher tests never really sleep.

    The YouTube / Bilibili fetchers call `_retry.throttle.wait(...)`
    before every network hit. With the production intervals (1-3s per
    host) the fetcher suites would spend most of their wall time
    sleeping — the YouTube tests actually did, silently, before this
    fixture existed. Restore a fresh production-config singleton after
    each test so throttle-behaviour tests that build their own instance
    are unaffected.
    """
    from prism_sidecar.fetchers import _retry

    _retry.reset_throttle_for_tests(intervals=[], default_interval=0.0)
    yield
    _retry.reset_throttle_for_tests()
