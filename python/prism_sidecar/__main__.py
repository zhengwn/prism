"""Entry point: `uv run prism-sidecar`."""

from __future__ import annotations

import argparse
import os

import uvicorn

from prism_sidecar import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="prism-sidecar", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument("--version", action="version", version=f"prism-sidecar {__version__}")
    args = parser.parse_args()

    # The lifespan banner reports the bind address from config.BIND_HOST /
    # BIND_PORT. Publish ours via env BEFORE uvicorn imports the app (the
    # string form defers that import to here) — env reaches --reload worker
    # subprocesses, module attributes don't.
    os.environ["PRISM_BIND_HOST"] = args.host
    os.environ["PRISM_BIND_PORT"] = str(args.port)

    uvicorn.run(
        "prism_sidecar.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
