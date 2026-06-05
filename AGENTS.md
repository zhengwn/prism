# AGENTS.md

> Prism — AI news & knowledge distiller. Cross-platform desktop app (Windows + macOS) built on Tauri 2.

## Setup commands

- **Install JS deps:** `npm install` (run from repo root)
- **Install Python deps:** `cd python && uv sync`
- **Start dev (Tauri shell + Vite + sidecar):** `npm run tauri:dev`
  - Tauri auto-spawns the Python sidecar via `uv run prism-sidecar`
  - Vite dev server on `http://localhost:1420`
  - Sidecar on `http://127.0.0.1:8765`
- **Run sidecar only (no Tauri):** `npm run sidecar:dev` — useful for debugging the Python layer
- **Build:** `npm run tauri:build` (bundles Tauri + frontend; sidecar bundling is a v0.4 task)
- **Frontend typecheck:** `npx tsc -b`
- **Frontend production build:** `npx vite build`
- **Rust check:** `cd src-tauri && cargo check`
- **Sidecar smoke test:** `npm run smoke` — boots the sidecar, hits `/health`, `/api/sources`, `/api/items`, validates the JSON shape, and tears the process down. Windows users can run `pwsh -File scripts/smoke.ps1` instead. Manual one-shot probe: `curl http://127.0.0.1:8765/health`.

## Project layout

```
prism/
├── src/                  # React + TS frontend (Vite)
│   ├── components/
│   │   ├── ui/           # shadcn-style base components (Button, Card, …)
│   │   └── layout/       # AppLayout, Sidebar, TopBar, DetailPanel
│   ├── pages/            # InboxPage, KnowledgePage, SourcesPage, SettingsPage
│   ├── lib/              # api.ts (sidecar client), utils.ts (cn, formatRelativeTime)
│   ├── store/            # Zustand global store
│   ├── styles/           # globals.css (Tailwind + CSS variables)
│   ├── types/            # Shared types — keep in sync with python/prism_sidecar/models.py
│   ├── App.tsx           # Router
│   └── main.tsx          # React entry
├── src-tauri/            # Tauri 2 Rust shell
│   ├── src/
│   │   ├── main.rs       # Binary entry
│   │   ├── lib.rs        # tauri::Builder setup
│   │   └── sidecar.rs    # Python sidecar spawn + IPC
│   ├── capabilities/     # Permissions (windows + commands)
│   ├── icons/            # App icons (placeholder PNGs for v0.1)
│   ├── Cargo.toml
│   └── tauri.conf.json
├── python/               # Python sidecar (FastAPI + uvicorn)
│   ├── pyproject.toml    # uv-managed, depends on fastapi + pydantic
│   ├── prism_sidecar/
│   │   ├── __main__.py   # CLI entry: `uv run prism-sidecar`
│   │   ├── app.py        # FastAPI app, routes, CORS
│   │   ├── models.py     # Pydantic models (mirror src/types/index.ts)
│   │   ├── store.py      # In-memory data layer (v0.1) / SQLite later
│   │   └── data/fixtures.py  # v0.1 seed data
│   └── README.md
├── docs/                 # ROADMAP.md, ARCHITECTURE.md
├── public/               # Static assets (favicon, etc.)
├── scripts/              # run-sidecar.sh and friends
├── BRAND.md              # Brand guide (name, slogan, visual direction)
└── README.md             # Project overview
```

## Code style

- **TypeScript:** strict mode, `tsc -b` for typecheck, no `any` unless documented why
- **React:** function components, hooks only, no class components; prefer `forwardRef` for UI primitives
- **Aliases:** `@/components`, `@/lib`, `@/store`, `@/types` (see `tsconfig.json` + `vite.config.ts`)
- **Tailwind:** shadcn-style — use `cn()` from `@/lib/utils` for class merging, prefer composition over custom CSS
- **Components:** all under `src/components/ui/` are leaf primitives; composite components go in `src/components/layout/`
- **Rust:** edition 2021, rust-version 1.77, idiomatic `tauri::Builder` pattern
- **Python:** Python 3.11+, type hints everywhere, Pydantic v2 for all data shapes
- **Commits:** conventional commits (`feat:` / `fix:` / `docs:` / `refactor:` / `chore:`)

## Testing instructions

- **v0.1 has no automated tests yet** — verify manually with:
  1. `npm run tauri:dev` boots the Tauri window
  2. Sidebar shows 4 sources from `python/prism_sidecar/data/fixtures.py`
  3. InboxPage lists 5 knowledge items
  4. Clicking an item opens the DetailPanel with summary + key points
  5. Search bar (top) filters items live
- **v0.2 will add:**
  - Vitest for React components
  - pytest for Python sidecar
  - Playwright for Tauri E2E

## PR & commit conventions

- **Branch from `main`**; never push to it directly
- **Commit message:** conventional commits (`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`)
- **Open PR via `gh pr create`** once `cargo check`, `tsc -b`, and `vite build` all pass
- **One logical change per PR** — don't bundle unrelated refactors

## Security

- **No secrets in git:** `.env*` is gitignored, and `secrets/` is too
- **Python sidecar listens on `127.0.0.1:8765` only** — never bind to `0.0.0.0` in dev
- **CORS allowlist** in `python/prism_sidecar/app.py` is restricted to Tauri + Vite origins
- **Tauri capabilities:** webview has minimal permissions by default; add new ones only when needed
- **API keys (LLM providers, RSS, etc.)** go to OS keychain in v0.2 — never in repo
- **User data (sources, items) stays on disk locally** — no telemetry in v0.1

## What agents should NOT do

- Don't modify `tauri.conf.json` productName/identifier without explicit user request
- Don't add npm/pip deps without asking — keep the dep tree small
- Don't commit `.env`, `secrets/`, or anything matching `*.pem` / `*.key`
- Don't reformat generated files (`package-lock.json`, `Cargo.lock`, `uv.lock`)
- Don't introduce a CSS-in-JS solution — Tailwind is the source of truth
- Don't add `@radix-ui/*` packages piecemeal — v0.2 will standardize the shadcn CLI flow
