"""End-to-end tests for /api/settings/* endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)

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
async def test_get_providers_returns_5(client):
    r = await client.get("/api/settings/providers")
    assert r.status_code == 200
    schemas = r.json()
    assert len(schemas) == 5
    ids = {s["id"] for s in schemas}
    assert ids == {"deepseek", "openai", "anthropic", "ollama", "custom"}


@pytest.mark.asyncio
async def test_get_providers_shape_for_deepseek(client):
    r = await client.get("/api/settings/providers")
    deepseek = next(s for s in r.json() if s["id"] == "deepseek")
    assert deepseek["label"] == "DeepSeek"
    assert deepseek["requiresKey"] is True
    assert deepseek["defaultModel"] == "deepseek/deepseek-chat"
    assert len(deepseek["fields"]) == 1
    assert deepseek["fields"][0]["name"] == "api_key"
    assert deepseek["fields"][0]["required"] is True


@pytest.mark.asyncio
async def test_get_providers_shape_for_ollama(client):
    r = await client.get("/api/settings/providers")
    ollama = next(s for s in r.json() if s["id"] == "ollama")
    assert ollama["requiresKey"] is False
    field_names = {f["name"] for f in ollama["fields"]}
    assert field_names == {"base_url", "model"}


@pytest.mark.asyncio
async def test_get_providers_shape_for_custom(client):
    r = await client.get("/api/settings/providers")
    custom = next(s for s in r.json() if s["id"] == "custom")
    field_names = {f["name"] for f in custom["fields"]}
    assert field_names == {"base_url", "model", "api_key"}


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
        json={"provider": "openai"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai"
    # No OPENAI_API_KEY in env → configured False.
    assert body["configured"] is False

    # The on-disk file is updated.
    path = settings_mod.ACTIVE_PROVIDER_PATH
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["provider"] == "openai"
    assert "api_key" not in payload  # paranoid — never store keys


@pytest.mark.asyncio
async def test_post_llm_persists_model_and_base_url(client):
    r = await client.post(
        "/api/settings/llm",
        json={
            "provider": "ollama",
            "model": "ollama/llama3.1:8b",
            "base_url": "http://192.168.1.5:11434",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "ollama"
    assert body["model"] == "ollama/llama3.1:8b"
    assert body["baseUrl"] == "http://192.168.1.5:11434"
    # Ollama is keyless → always configured (as long as base_url is
    # supplied).
    assert body["configured"] is True


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
        json={"provider": "openai", "api_key": "sk-stolen"},
    )
    # Two possible paths: Pydantic rejects via extra="forbid" (422) or
    # we get past and our explicit check returns 400. Either is correct.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_post_then_get_round_trips(client):
    r1 = await client.post(
        "/api/settings/llm",
        json={"provider": "anthropic", "model": "anthropic/claude-3-haiku-20240307"},
    )
    assert r1.status_code == 200
    r2 = await client.get("/api/settings/llm")
    assert r2.status_code == 200
    body = r2.json()
    assert body["provider"] == "anthropic"
    assert body["model"] == "anthropic/claude-3-haiku-20240307"


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
    cfg = settings_mod.set_active_provider("ollama", model="ollama/llama3", base_url="http://x:11434")
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "ollama/llama3"
    assert cfg["base_url"] == "http://x:11434"
    # Re-read from disk to confirm persistence.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["provider"] == "ollama"


def test_set_active_provider_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", tmp_path / "ap.json")
    with pytest.raises(ValueError):
        settings_mod.set_active_provider("llamastack")


def test_is_provider_configured_for_keyless(tmp_path, monkeypatch):
    """Ollama has no key env var → always configured."""
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    assert settings_mod.is_provider_configured("ollama") is True


def test_is_provider_configured_for_keyed(monkeypatch):
    """DeepSeek needs DEEPSEEK_API_KEY; absent → not configured."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert settings_mod.is_provider_configured("deepseek") is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    assert settings_mod.is_provider_configured("deepseek") is True


def test_get_llm_status_shape(tmp_path, monkeypatch):
    path = tmp_path / "ap.json"
    monkeypatch.setattr(settings_mod, "ACTIVE_PROVIDER_PATH", path)
    path.write_text(json.dumps({"provider": "ollama", "model": "ollama/m", "base_url": "http://x:11434"}))
    status = settings_mod.get_llm_status()
    assert status.provider == "ollama"
    assert status.model == "ollama/m"
    assert status.base_url == "http://x:11434"
    assert status.configured is True
