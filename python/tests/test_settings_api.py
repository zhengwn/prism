"""End-to-end tests for /api/settings/* endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from prism_sidecar import settings as settings_mod
from prism_sidecar.app import app


@pytest.fixture
async def client(monkeypatch, tmp_path: Path):
    """Boot the app with a fresh, isolated data dir and no API keys."""
    from prism_sidecar import config
    from prism_sidecar import db as dbmod

    await dbmod.close_db()

    data_dir = tmp_path / "prism-settings-test"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISM_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(config, "PRISM_DB_PATH", data_dir / "data.db")
    monkeypatch.setattr(dbmod, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod, "PRISM_DB_PATH", data_dir / "data.db")
    # Ensure no key is set for any provider.
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    # Re-point the settings module at the tmp data dir.
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", data_dir / "active_provider.json")
    monkeypatch.setattr(settings_mod, "PRISM_DATA_DIR", data_dir)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    await dbmod.close_db()


# ---- /api/settings/providers --------------------------------------------


@pytest.mark.asyncio
async def test_get_providers_returns_2(client):
    r = await client.get("/api/settings/providers")
    assert r.status_code == 200
    schemas = r.json()
    assert len(schemas) == 2
    ids = {s["id"] for s in schemas}
    assert ids == {"deepseek", "minimax"}


@pytest.mark.asyncio
async def test_get_providers_shape_for_deepseek(client):
    r = await client.get("/api/settings/providers")
    deepseek = next(s for s in r.json() if s["id"] == "deepseek")
    assert deepseek["label"] == "DeepSeek"
    assert deepseek["requiresKey"] is True
    # defaultModel is the user-facing id (no litellm prefix leaking).
    assert deepseek["defaultModel"] == "deepseek-v4-pro"
    assert len(deepseek["fields"]) == 1
    assert deepseek["fields"][0]["name"] == "api_key"
    assert deepseek["fields"][0]["required"] is True


@pytest.mark.asyncio
async def test_get_providers_shape_for_minimax(client):
    r = await client.get("/api/settings/providers")
    mm = next(s for s in r.json() if s["id"] == "minimax")
    assert mm["requiresKey"] is True
    # defaultModel is the user-facing id (no "openai/" litellm prefix).
    assert mm["defaultModel"] == "MiniMax-M3"
    assert len(mm["fields"]) == 1
    assert mm["fields"][0]["name"] == "api_key"
    assert mm["fields"][0]["required"] is True


# ---- /api/settings/llm GET ----------------------------------------------


@pytest.mark.asyncio
async def test_get_llm_default(client):
    """On first run, active provider is deepseek and configured=False
    (no env key)."""
    r = await client.get("/api/settings/llm")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "deepseek"
    assert body["configured"] is False


@pytest.mark.asyncio
async def test_get_llm_reflects_env_key(monkeypatch, tmp_path):
    """When the env has the matching key, configured becomes True."""
    from prism_sidecar import config
    from prism_sidecar import db as dbmod
    from prism_sidecar.app import app

    await dbmod.close_db()
    data_dir = tmp_path / "prism-env-test"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISM_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(config, "PRISM_DB_PATH", data_dir / "data.db")
    monkeypatch.setattr(dbmod, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod, "PRISM_DB_PATH", data_dir / "data.db")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")

    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", data_dir / "active_provider.json")

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/settings/llm")
            assert r.status_code == 200
            body = r.json()
            assert body["provider"] == "deepseek"
            assert body["configured"] is True

    await dbmod.close_db()


# ---- /api/settings/llm POST ---------------------------------------------


@pytest.mark.asyncio
async def test_post_llm_switches_provider(client):
    r = await client.post(
        "/api/settings/llm",
        json={"provider": "minimax"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "minimax"
    # No MINIMAX_API_KEY in env → configured False.
    assert body["configured"] is False

    # The on-disk file is updated.
    path = settings_mod.ACTIVE_PROVIDER_PATH
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["provider"] == "minimax"
    assert "api_key" not in payload  # paranoid — never store keys


@pytest.mark.asyncio
async def test_post_llm_persists_model_and_base_url(client):
    r = await client.post(
        "/api/settings/llm",
        json={
            "provider": "minimax",
            "model": "M3-highspeed",
            "base_url": "https://mirror.example/v1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "minimax"
    assert body["model"] == "M3-highspeed"
    assert body["baseUrl"] == "https://mirror.example/v1"


@pytest.mark.asyncio
async def test_post_llm_rejects_unknown_provider(client):
    r = await client.post(
        "/api/settings/llm",
        json={"provider": "llamastack"},
    )
    # Pydantic's extra="forbid" rejects unknown fields; an unknown
    # provider id flows through the schema so we hit the ValueError
    # branch with 400.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_post_llm_rejects_api_key_in_body(client):
    """The api_key must never transit the sidecar — even if the client
    somehow sneaks it past Pydantic's exclude=True, we 400."""
    # We bypass Pydantic by sending extra fields in the raw JSON.
    r = await client.post(
        "/api/settings/llm",
        json={"provider": "minimax", "api_key": "sk-stolen"},
    )
    # Two possible paths: Pydantic rejects via extra="forbid" (422) or
    # we get past and our explicit check returns 400. Either is correct.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_post_then_get_round_trips(client):
    r1 = await client.post(
        "/api/settings/llm",
        json={"provider": "minimax", "model": "M3-highspeed"},
    )
    assert r1.status_code == 200
    r2 = await client.get("/api/settings/llm")
    assert r2.status_code == 200
    body = r2.json()
    assert body["provider"] == "minimax"
    assert body["model"] == "M3-highspeed"


# ---- lifespan writes default file --------------------------------------


@pytest.mark.asyncio
async def test_lifespan_creates_default_active_provider_file(monkeypatch, tmp_path):
    """The first sidecar start writes the default active_provider.json
    if it doesn't exist."""
    from prism_sidecar import config
    from prism_sidecar import db as dbmod
    from prism_sidecar.app import app

    await dbmod.close_db()
    data_dir = tmp_path / "prism-default"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISM_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(config, "PRISM_DB_PATH", data_dir / "data.db")
    monkeypatch.setattr(dbmod, "PRISM_DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod, "PRISM_DB_PATH", data_dir / "data.db")
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", data_dir / "active_provider.json")

    assert not (data_dir / "active_provider.json").exists()

    async with LifespanManager(app):
        pass  # just let the lifespan run

    await dbmod.close_db()
    # The file should now exist with the default provider.
    assert (data_dir / "active_provider.json").exists()
    payload = json.loads((data_dir / "active_provider.json").read_text(encoding="utf-8"))
    assert payload["provider"] == "deepseek"


# ---- settings module unit tests ----------------------------------------


def test_load_active_provider_returns_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", tmp_path / "nope.json")
    cfg = settings_mod.load_active_provider()
    assert cfg == {"provider": "deepseek"}


def test_set_active_provider_persists(tmp_path, monkeypatch):
    path = tmp_path / "ap.json"
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", path)
    cfg = settings_mod.set_active_provider("minimax", model="M3-highspeed", base_url="https://x/v1")
    assert cfg["provider"] == "minimax"
    assert cfg["model"] == "M3-highspeed"
    assert cfg["base_url"] == "https://x/v1"
    # Re-read from disk to confirm persistence.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["provider"] == "minimax"


def test_set_active_provider_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", tmp_path / "ap.json")
    with pytest.raises(ValueError):
        settings_mod.set_active_provider("llamastack")


def test_is_provider_configured_for_keyed(monkeypatch):
    """DeepSeek needs DEEPSEEK_API_KEY; absent → not configured."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert settings_mod.is_provider_configured("deepseek") is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    assert settings_mod.is_provider_configured("deepseek") is True

    """MiniMax needs MINIMAX_API_KEY; absent → not configured."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert settings_mod.is_provider_configured("minimax") is False
    monkeypatch.setenv("MINIMAX_API_KEY", "ey-x")
    assert settings_mod.is_provider_configured("minimax") is True


def test_get_llm_status_shape(tmp_path, monkeypatch):
    path = tmp_path / "ap.json"
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", path)
    path.write_text(json.dumps({"provider": "minimax", "model": "M3", "base_url": "https://x/v1"}))
    status = settings_mod.get_llm_status()
    assert status.provider == "minimax"
    assert status.model == "M3"
    assert status.base_url == "https://x/v1"


# ---- resolve_base_url -----------------------------------------------------
#
# The startup banner used to print `active_provider.json`'s `base_url`
# verbatim, so an env override (which Tauri injects at spawn) showed up as
# `base_url=None` even though the distiller was honouring it. These pin the
# precedence: marker file > env > provider default.


def test_resolve_base_url_prefers_marker_over_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_BASE", "https://env-host/v1")
    assert (
        settings_mod.resolve_base_url("minimax", "https://marker-host/v1")
        == "https://marker-host/v1"
    )


def test_resolve_base_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_BASE", "https://env-host/v1")
    assert settings_mod.resolve_base_url("minimax", None) == "https://env-host/v1"


def test_resolve_base_url_none_when_nothing_set(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_BASE", raising=False)
    assert settings_mod.resolve_base_url("minimax", None) is None


def test_resolve_base_url_ignores_env_for_provider_without_override(monkeypatch):
    # deepseek has no base-url env var; MINIMAX_API_BASE must not leak into it.
    monkeypatch.setenv("MINIMAX_API_BASE", "https://env-host/v1")
    assert settings_mod.resolve_base_url("deepseek", None) is None
