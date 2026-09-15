"""Regression tests for the CLI entrypoint's bind-arg → banner propagation.

The lifespan banner reports its bind address from ``config.BIND_HOST`` /
``config.BIND_PORT``; ``main()`` must publish ``--host`` / ``--port`` into
the environment *before* uvicorn imports the app. The v0.5.0 banner
hardcoded ``127.0.0.1:8765`` and lied for any non-default bind.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from prism_sidecar.__main__ import main


@pytest.fixture
def uvicorn_call(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``uvicorn.run``; capture its kwargs + the env main() published."""
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        captured["app"] = app
        captured["env_host"] = os.environ.get("PRISM_BIND_HOST")
        captured["env_port"] = os.environ.get("PRISM_BIND_PORT")
        captured.update(kwargs)

    # Pre-seed so monkeypatch restores the caller's env after main() overwrites.
    monkeypatch.setenv("PRISM_BIND_HOST", "unset-by-test")
    monkeypatch.setenv("PRISM_BIND_PORT", "unset-by-test")
    monkeypatch.setattr("prism_sidecar.__main__.uvicorn.run", fake_run)
    return captured


def test_custom_bind_args_reach_env_and_uvicorn(monkeypatch: pytest.MonkeyPatch, uvicorn_call: dict[str, Any]) -> None:
    monkeypatch.setattr("sys.argv", ["prism-sidecar", "--host", "0.0.0.0", "--port", "9001"])
    main()
    assert uvicorn_call["env_host"] == "0.0.0.0"
    assert uvicorn_call["env_port"] == "9001"
    assert uvicorn_call["host"] == "0.0.0.0"
    assert uvicorn_call["port"] == 9001
    assert uvicorn_call["app"] == "prism_sidecar.app:app"


def test_default_bind_matches_cli_defaults(monkeypatch: pytest.MonkeyPatch, uvicorn_call: dict[str, Any]) -> None:
    monkeypatch.setattr("sys.argv", ["prism-sidecar"])
    main()
    assert uvicorn_call["env_host"] == "127.0.0.1"
    assert uvicorn_call["env_port"] == "8765"
    assert uvicorn_call["host"] == "127.0.0.1"
    assert uvicorn_call["port"] == 8765
