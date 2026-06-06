# AGENTS.md

> Prism — AI news & knowledge distiller. Cross-platform desktop app (Windows + macOS) built on Tauri 2.

## Setup commands

- **Install JS deps:** `npm install` (run from repo root)
- **Install Python deps:** `cd python && uv sync`
- **Start dev (Tauri shell + Vite + sidecar):** `npm run tauri:dev`
  - Tauri auto-spawns the Python sidecar via `uv run prism-sidecar`
  - Vite dev server on `http://localhost:1420`
  - Sidecar on `http://127.0.0.1:8765`
- **Recover from a stuck dev server (`dev:clean`):** if `tauri:dev` is killed
  uncleanly (force-quit, crash, lost terminal), ports 1420 / 8765 may stay
  bound by orphan vite / prism-sidecar processes and the next `tauri:dev`
  will fail with `Port 1420 is already in use`. Run `npm run dev:clean` —
  it kills whoever is holding those two ports and re-launches dev. Use this
  only when dev is not running cleanly; do not run it while a healthy dev
  session is up.
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
│   ├── pyproject.toml    # uv-managed, depends on fastapi + pydantic + aiosqlite + httpx + feedparser + litellm + apscheduler
│   ├── pytest.ini        # pytest 配置
│   ├── prism_sidecar/
│   │   ├── __main__.py   # CLI entry: `uv run prism-sidecar`
│   │   ├── app.py        # FastAPI app, routes, CORS
│   │   ├── models.py     # Pydantic v2 models with bilingual KnowledgeItem (mirror src/types/index.ts)
│   │   ├── db.py         # aiosqlite + schema migration (v0.2a)
│   │   ├── store.py      # SQLite-backed CRUD (v0.2a)
│   │   ├── scheduler.py  # APScheduler integration (v0.2a)
│   │   ├── config.py     # env-based config (DEEPSEEK_API_KEY, PRISM_DATA_DIR, …)
│   │   ├── fetchers/     # Fetcher Protocol + RSS + HackerNews (v0.2a)
│   │   ├── distillers/   # Distiller Protocol + DeepSeek via litellm (v0.2a)
│   │   ├── pipeline/     # sync orchestration (v0.2a)
│   │   └── data/fixtures.py  # 5 seed sources (HN + 4 RSS)
│   ├── tests/            # pytest 38 case (rss/hn/distiller/store/sync/api)
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

## Product invariants (apply to ALL new code, not optional)

These are non-negotiable design rules. Every PR that touches UI or
content rendering must satisfy them — reviewers should block on
violations. They are project-specific product decisions, not generic
best-practice, so they live here rather than in agent memory.

- **i18n is mandatory.** Every user-visible string in `src/components/` and
  `src/pages/` must go through the `t()` hook from `useLanguage`. Hard-coded
  English in JSX is a defect, not a style choice. When adding a new key,
  populate BOTH `src/i18n/en.json` AND `src/i18n/zh.json` in the same
  commit — never ship an English-only key. The brand string "Prism" and
  short technical tokens (icons, kbd shortcuts) are exempt; everything
  else is translated. User-supplied content (article titles, summaries,
  tags) is NOT translated — that is data, not chrome.
- **Both themes must work.** Every UI element must look correct in light
  AND dark mode (and the "system" / follow-OS mode, which spans both).
  Use semantic Tailwind tokens that map through the CSS variables in
  `src/styles/globals.css` — `bg-background`, `text-foreground`,
  `border-border`, `bg-card`, `text-muted-foreground`, etc. NEVER hard-code
  hex colors, raw `hsl(...)` values, or `dark:` / `light:`-only utility
  classes. If a new color is needed, add a variable to BOTH `:root` and
  `.dark` blocks in `globals.css` first, then map it in `tailwind.config.js`.
- **Adding a language:** see the docstring in `src/i18n/index.ts` for the
  4-step checklist (json file → Language union → register resource → label).
- **Adding a theme mode:** extend the `Theme` union in `src/lib/theme.ts`,
  add the corresponding `.dark` (or `:root`) block in `globals.css` if
  the resolution is novel, and update the Settings picker.

## Testing instructions

- **v0.2a 测试覆盖**（已实现）：
  - **Python sidecar**：`cd python && uv run pytest -v` — 38 case（rss 5 / hn 3 / distiller 8 / store 8 / sync 5 / api 9）
  - **React 组件**：`cd src && npx vitest run` — 7 case（Button / InboxPage Sync 按钮 / SourcesPage Add Source dialog）
  - **Rust keychain**：`cd src-tauri && cargo test --test keychain_smoke` — 2 真集成测试（macOS Keychain roundtrip）
  - **端到端**：`npm run smoke` — 启动 sidecar → 同步 → 验 items
- **手动验证 v0.2a**：
  1. `npm run tauri:dev` 启动 Tauri 窗口
  2. Sidebar 显示 5 个种子源（HN + Simon + OpenAI + DeepMind + HF）
  3. 顶栏点「立即同步」按钮，触发 sync，验证 items 列表刷新
  4. SourcesPage 点 `+` 弹窗加一个新 RSS 源，验证列表更新
  5. SettingsPage 配置 DeepSeek API key（存 OS keychain），重启 app 后状态显示「已配置」
- **v0.2b 起加：** Playwright for Tauri E2E（开 Tauri 窗口跑真实交互）

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
- **API keys (LLM providers, RSS, etc.)** go to **OS keychain** via `tauri-plugin-keyring` (v0.2a 已实现) — never in repo, never in sidecar config files
- **Key exposure boundary:** 前端只能调 `getApiKeyStatus()` 拿到 `{configured: boolean}`，**永远拿不到 key 值**；key 仅在 Tauri 启动 sidecar 时通过 `cmd.env("DEEPSEEK_API_KEY", key)` 注入子进程环境变量
- **User data (sources, items) stays on disk locally** (`~/.prism/data.db`) — no telemetry in v0.2a

## Theme system (v0.1)

Three-state model: `light` / `dark` / `system`. `system` live-follows
`prefers-color-scheme`. The code is split into three layers — the split is
what makes FOUC-free first paint possible:

| File | Role | When it runs |
|---|---|---|
| `index.html` inline `<script>` | FOUC bootstrap: reads `localStorage["prism-theme"]`, applies `.dark` + `color-scheme` to `<html>` before the first paint. Mirrors `src/lib/theme.ts` — **keep them in sync**. | Before `<body>` renders, before React loads |
| `src/lib/theme.ts` | Framework-free helpers: `Theme` / `ResolvedTheme` types, `getStoredTheme` / `setStoredTheme` / `getSystemTheme` / `resolveTheme` / `applyTheme`. | Module load (zero deps) |
| `src/hooks/useTheme.ts` | React hook: `useState` for `theme` + `resolvedTheme`, `useEffect` to re-apply on change, `matchMedia` listener for live OS-following in `system` mode. | First import by any UI consumer |

`tailwind.config.js` uses `darkMode: ["class"]`; CSS variables in
`src/styles/globals.css` (`:root` and `.dark`) already define the palette —
**add new theme tokens there, never hardcode colors in components**.

When extending (e.g. a "high-contrast" mode in v0.2+): extend the `Theme`
union in `src/lib/theme.ts`, then update both the inline script in
`index.html` and the Settings picker.

## What agents should NOT do

- Don't modify `tauri.conf.json` productName/identifier without explicit user request
- Don't add npm/pip deps without asking — keep the dep tree small
- Don't commit `.env`, `secrets/`, or anything matching `*.pem` / `*.key`
- Don't reformat generated files (`package-lock.json`, `Cargo.lock`, `uv.lock`)
- Don't introduce a CSS-in-JS solution — Tailwind is the source of truth
- Don't add `@radix-ui/*` packages piecemeal — v0.2 will standardize the shadcn CLI flow
