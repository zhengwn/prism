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

- **v0.2b 测试覆盖**（已实现，v0.2a 之后 +76 case）：
  - **Python sidecar**：`cd python && uv run pytest -v` — 114 case（rss 5 / hn 3 / distiller 8 / store 8 / sync 5 / api 9 → v0.2b 新增 FTS5 14 + cancel 3 + smart-quote 解析等 38+）
  - **React 组件**：`cd src && npx vitest run` — 32 case（v0.2a 7 + v0.2b 新增 inline-markdown 10 + InboxPage 改写 8 + Settings / Progress 7 等）
  - **Rust keystore**：`cd src-tauri && cargo test --test keystore_smoke` — 8 case（roundtrip / 0600 perms / 损坏容错 / 并发 / key_last4 / active-provider 校验 / 迁移幂等 / 真 macOS Keychain migration roundtrip）
  - **端到端**：`npm run smoke` — 启动 sidecar → 同步 → 验 items
- **手动验证 v0.2b**：
  1. `npm run tauri:dev` 启动 Tauri 窗口
  2. Sidebar 显示 5 个种子源（HN + Simon + OpenAI + DeepMind + HF）
  3. 顶栏点「立即同步」按钮，触发 sync，验证 items 列表刷新
  4. SourcesPage 点 `+` 弹窗加一个新 RSS 源，验证列表更新
  5. SettingsPage 配置 DeepSeek API key（写入 `~/.prism/keystore.json` 加密存储），重启 app 后状态显示「已配置」
  6. InboxPage 顶部搜索框输入中文 / 英文关键词，验证 FTS5 搜索 ~5ms 命中（v0.2b）
  7. 蒸馏中点「取消」按钮，验证蓝色 toast + 进度条停止（v0.2b）
  8. 同步中点 sync 按钮（此时变「取消」），验证下一个 source 边界停止（v0.2b）
- **v0.2c 起加：** Playwright for Tauri E2E（开 Tauri 窗口跑真实交互）

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
- **API keys (LLM providers, RSS, etc.)** go to a **local encrypted-file
  keystore** at `~/.prism/keystore.json` (master key at
  `~/.prism/keystore.key`, 0600 on Unix). Encrypted with AES-256-GCM
  (random nonce per write, base64-encoded `nonce || ciphertext` blob).
  The previous OS-keychain layout (`tauri-plugin-keyring`) was removed
  because it triggered an "Allow access to keychain" macOS prompt on
  every launch; the new layout has no OS prompt after the first
  migration run. See `src-tauri/src/keystore.rs` for the on-disk
  format, encryption details, and migration logic.
- **Key exposure boundary:** 前端只能调 `getApiKeyStatus()` /
  `get_llm_config()` 拿到 `{configured: boolean}`，**永远拿不到 key
  值**；key 仅在 Tauri 启动 sidecar 时通过
  `cmd.env("DEEPSEEK_API_KEY", key)` 注入子进程环境变量
- **One-shot keychain→keystore migration:** `keystore::migrate_from_keychain_if_needed`
  is called once in `lib.rs` setup, **before** the sidecar is spawned.
  Idempotent — once `~/.prism/keystore.json` exists, the migration is
  a no-op. The first call on a v0.2a-or-earlier install triggers one
  macOS prompt and then deletes the legacy keychain entries, so
  subsequent launches are prompt-free.
- **User data (sources, items) stays on disk locally** (`~/.prism/data.db`) — no telemetry in v0.2b

## LLM provider architecture (v0.2a+)

Sidecar supports **2 LLM providers** behind one `LitellmDistiller`
base class — pruned from the original 5-provider v0.2a design. **Read
this before adding a 3rd provider or touching the distiller layer.**

```
python/prism_sidecar/
  settings.py                # PROVIDER_SCHEMAS + active_provider.json R/W
  distillers/
    base.py                  # LitellmDistiller + looks_like_key_invalid
    deepseek.py              # DeepSeekDistiller
    minimax.py               # MiniMaxDistiller (OpenAI-compatible protocol)
    registry.py              # get_distiller(provider, model?, base_url?)
```

| Provider id | Default model           | Env key var          | Default base URL                  |
|-------------|-------------------------|----------------------|-----------------------------------|
| `deepseek`  | `deepseek-v4-pro`       | `DEEPSEEK_API_KEY`   | (canonical, no override)          |
| `minimax`   | `MiniMax-M3`            | `MINIMAX_API_KEY`    | `https://api.minimaxi.com/v1`     |

> **Model id 命名约定**：`defaultModel` 跟用户填的 override 都是**用户友好的
> 产品名**（不带 `deepseek/` 或 `openai/` 这种 litellm 路由前缀）。各 distiller
> 的 `__init__` 会在内部自动加前缀再传给 litellm——用户不接触 implementation
> detail。如果用户 override 时把前缀也写上（比如从老的 v0.2a 配置文件
> 复制过来），`removeprefix(...)` 会先剥掉再重加，不会双前缀。

**Adding a new provider** (3 mandatory steps + tests):
1. `distillers/<name>.py` — subclass `LitellmDistiller`; set
   `provider_name`, `default_model` (full litellm prefix like
   `"mistral/mistral-large-latest"`), `env_key_var` (or `None` for
   keyless). Override `_extra_litellm_kwargs()` only if it needs a
   non-default `api_base` or other kwarg.
2. `distillers/registry.py` — add to `PROVIDERS` dict.
3. `settings.py` — append a `ProviderSchema(...)` to `PROVIDER_SCHEMAS`
   AND add the env-var name to `_PROVIDER_ENV_KEY` (or `None` for
   keyless). Then frontend + i18n keys (`hint<Name>` etc.) — owned by
   `frontend-expert`.

**Provider-specific invariants (do not break):**
- **MiniMax wraps with `openai/` prefix.** User passes `"M3-highspeed"`,
  `MiniMaxDistiller` produces `openai/M3-highspeed` so it routes
  through litellm's OpenAI-compatible adapter against
  `https://api.minimaxi.com/v1`. If the user already typed
  `openai/...`, leave it alone.
- **`env_key_var = None` is the keyless path** (no provider uses it
  today, but the base class's `if self.env_key_var is not None: ...`
  branch must stay). Pass `api_key=None` to litellm, not `""` (empty
  string is "invalid api key" to litellm).
- **Distillers are read from env, never from disk.** Active
  provider id is in `~/.prism/active_provider.json`; the key is in
  `~/.prism/keystore.json` (encrypted with AES-256-GCM under
  `~/.prism/keystore.key`); Tauri injects it as env var when
  spawning the sidecar. Sidecar never reads the keystore directly.
- **POST `/api/settings/llm` hard-rejects any `apiKey` field**
  (HTTP 400). Keys transit only through Tauri's keystore + env
  injection — never through sidecar HTTP. The
  `LlmConfigUpdate.api_key = Field(exclude=True)` + the route's
  explicit `if payload.api_key not in (None, "")` check are
  intentionally belt-and-suspenders; keep both layers.
- **camelCase JSON is the contract.** Pydantic models use
  `ConfigDict(alias_generator=to_camel, populate_by_name=True)` on
  `_CamelBase`. Routes use `response_model_by_alias=True`. The
  frontend's `src/types/index.ts` matches these aliased names —
  if you add a new field, update both sides.
- **Lifespan writes the default file.** First start of a fresh
  install creates `~/.prism/active_provider.json` with
  `{"provider": "deepseek"}` (no model/base_url). The
  `pipeline/sync.py` and `pipeline/distill.py` `is_distiller_configured()`
  helper (in `config.py`) is the **legacy v0.1** DeepSeek-only
  check; new code should use `settings.is_provider_configured(provider)`
  which checks the *active* provider's env var.

**Active-provider change requires a sidecar restart (Tauri-owned).**
The `POST /api/settings/llm` endpoint does a best-effort in-process
hot-swap of a cached distiller reference, but the pipeline rebuilds
the distiller per job from the on-disk file, and key env vars don't
change without a process restart. The reliable flow is
Tauri→keystore write→`sidecar::restart()` (kill + respawn with
fresh env) — owned by `tauri-expert` (see commit `e460389`).

**Tauri-side `KNOWN_PROVIDERS`** in `src-tauri/src/secrets.rs` must
stay in lockstep with the Python `PROVIDER_SCHEMAS` list. The
`default_model_for()` and `get_provider_schema()` Tauri commands
both hard-code the same 2 providers — they're the Settings-UI
fallback when the sidecar isn't up yet.

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
