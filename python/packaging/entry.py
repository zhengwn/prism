"""PyInstaller entry point for the frozen sidecar binary.

A dedicated one-liner rather than pointing PyInstaller at
``prism_sidecar/__main__.py`` directly — freezing a package ``__main__``
module invites relative-import / ``__package__`` quirks. This plain script
calls the same ``main()`` the ``prism-sidecar`` console script uses, so the
frozen binary behaves identically to ``uv run prism-sidecar`` (argparse
``--host`` / ``--port`` / ``--version``).

Note: ``__main__.py`` starts uvicorn with the import STRING
``"prism_sidecar.app:app"`` — a dynamic import PyInstaller can't see. The
freeze script pulls the whole ``prism_sidecar`` package in via
``--collect-submodules prism_sidecar`` (plus an explicit ``prism_sidecar.app``
hidden import) so that string resolves inside the frozen bundle.
"""

from prism_sidecar.__main__ import main

if __name__ == "__main__":
    main()
