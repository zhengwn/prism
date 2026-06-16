# Prism — Architecture

> 截至 v0.2b（v0.2a + v0.2b 基础设施重构 + UX 打磨）。v0.2c 起加多源补齐，v0.3 之后会加 MCP server、sqlite-vec 语义搜索、Skill bundle 等。

## 总览

```
┌──────────────────────────────────────────────────────────────┐
│  Tauri Shell (Rust)                                           │
│  ┌──────────────────┐  IPC (Tauri commands)  ┌────────────┐  │
│  │  WebView         │◄──────────────────────►│  Rust Core │  │
│  │  React + TS UI   │                        │            │  │
│  │                  │   invoke()             │  - window  │  │
│  │  - Sync 按钮     │                        │  - lifecycle│ │
│  │  - Add Source    │                        │  - keystore │  │
│  │  - 双语显示      │                        │  - sidecar  │  │
│  │  - Settings      │                        │    (spawn   │  │
│  │                  │                        │     + env   │  │
│  │                  │                        │     + kill) │  │
│  └────────┬─────────┘                        └─────┬──────┘  │
└───────────┼────────────────────────────────────────┼─────────┘
            │ HTTP (loopback 127.0.0.1:8765)        │ std::process::Command
            ▼                                        ▼
                              ┌──────────────────────────┐
                              │  Python Sidecar          │
                              │  (FastAPI + uvicorn)     │
                              │                          │
                              │  REST:                   │
                              │   /health                │
                              │   /api/sources (CRUD)    │
                              │   /api/sources/{id}      │
                              │   /api/items             │
                              │   /api/sync, /api/sync/* │
                              │   /api/sync/history      │
                              │                          │
                              │  Pipeline:               │
                              │   fetchers/ (rss + hn)   │
                              │   distillers/ (deepseek) │
                              │   pipeline/sync          │
                              │   scheduler              │
                              └──────────┬───────────────┘
                                         │ aiosqlite
                                         ▼
                              ┌──────────────────────────┐
                              │  ~/.prism/data.db        │
                              │  - sources               │
                              │  - items (bilingual)     │
                              │  - sync_log              │
                              │  - sync_jobs             │
                              │  (sqlite-vec 留 v0.5)    │
                              └──────────────────────────┘
```

## 进程边界

- **Tauri 壳** 与 **Python sidecar** 是两个独立进程，通过 `127.0.0.1:8765` HTTP 通信
- 优点：
  - 调试方便（curl / Postman / Vite dev 都能直接打）
  - Tauri 不会因为 Python 崩溃而崩
  - Python 端可以独立升级（不重新打包 Tauri）
- 缺点：
  - 多一个进程
  - 跨平台打包需要打两个东西（v0.4 处理）

## 数据流

### 抓取 + 提炼（v0.2a 引入，v0.2b 完善）

```
[Source]   → fetcher.fetch(source)  → RawItem[]
              ├─ RSSFetcher:     feedparser + httpx, retry/backoff, HTML strip
              └─ HackerNewsFetcher: Algolia API，多关键词池去重
                ↓
            pipeline.run_source_sync(source)
              ├─ 遍历 RawItem
              ├─ items.url unique → skip 已存在
              ├─ insert raw item (distilled_at=NULL)
              └─ if distiller configured:
                   DeepSeekDistiller.distill(raw)
                     ├─ litellm.acompletion → JSON {title_zh, summary_zh, key_points_zh, tags_zh}
                     ├─ parse + validate
                     └─ update_item(item.id, distilled)
                ↓
            SQLite (sources, items, sync_log)
                ↓
            [GET /api/items?source_id=&status=&q=&limit=&offset=] → React UI
```

### 手动 vs 自动同步

- **手动**：`POST /api/sync`（全源）或 `POST /api/sync/{source_id}`（单源）→ 返回 `SyncResult`
  - 并发保护：in-memory `_inflight_jobs` set，第二次请求返回 409 Conflict
- **自动**：APScheduler `AsyncIOScheduler` 在 FastAPI lifespan 启动
  - `cron(hour=9, timezone="Asia/Shanghai")` 每天 9 点跑 `run_all_sync()`
  - 单源失败不影响其他源（每个源独立 `try/except` + 写 `sync_log.error`）

### 蒸馏失败处理

- 提炼失败时 item **仍写入 DB**（`distilled_at=NULL`），UI 显示「待提炼」角标
- 失败原因：API key 未配置 / 网络错误 / JSON 解析失败 / rate limit
- 失败时 `sync_log.items_distilled` 不递增，但 `items_new` 仍计

### Agent 调用（v0.3，未实现）

```
[Claude Code / Cursor] → MCP client (stdio) → prism mcp server
                                             → [search, read, subscribe tools]
                                             → returns KnowledgeItem
```

## API 契约（v0.2a 引入，v0.2b 扩展）

### Sources

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/sources` | — | `Source[]` |
| POST | `/api/sources` | `SourceCreate` | `Source` |
| GET | `/api/sources/{id}` | — | `Source` |
| **PATCH** | `/api/sources/{id}` | `SourcePatch` | `Source`（v0.2a 引入） |
| DELETE | `/api/sources/{id}` | — | `{ok: true}` |

### Items

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/api/items` | `source_id, status, q, limit, offset` | `KnowledgeItem[]` |
| GET | `/api/items/{id}` | — | `KnowledgeItem` |

### Sync

| Method | Path | Returns | 备注 |
|---|---|---|---|
| POST | `/api/sync` | `SyncResult` | 全源；v0.2a 真跑（v0.1 是 no-op），v0.2b 异步化（`{jobId}` 立即返回） |
| POST | `/api/sync/{source_id}` | `SyncResult` | 单源 |
| GET | `/api/sync/{job_id}` | `SyncJob` | 查 job 状态（`running` / `cancelled` / `success` / `error`） |
| **POST** | **`/api/sync/{job_id}/cancel`** | `{ok: true}` | **v0.2b 新增**：per-source 边界检查点，长跑可中断 |
| GET | `/api/sync/history?limit=10` | `SyncLog[]` | 历史 |

### Distill（v0.2b 新增）

| Method | Path | Returns | 备注 |
|---|---|---|---|
| GET | `/api/distill/status` | `DistillStatus` | 整体提炼进度（running / pending / done / error 计数） |
| GET | `/api/distill/status/stream` | `text/event-stream` (SSE) | v0.2b 实时进度推送，前端 `useDistillProgress` hook 订阅 |
| POST | `/api/distill/cancel` | `{ok: true}` | v0.2b 取消当前跑批（next item 边界停） |

`SyncResult` 字段：`jobId, status, startedAt, finishedAt, sourcesTotal, sourcesDone, itemsNew, itemsDistilled, error`（camelCase 通过 `_CamelBase` 转换）。

### 双语 KnowledgeItem

```typescript
interface KnowledgeItem {
  // 兼容字段（display 用）
  title: string;            // = titleZh ?? titleEn
  summary?: string;         // = summaryZh ?? summaryEn
  keyPoints?: string[];     // = keyPointsZh
  tags?: string[];          // = tagsZh
  // 双语（持久化）
  titleEn: string;
  titleZh?: string;
  summaryEn?: string;
  summaryZh?: string;
  keyPointsZh?: string[];
  tagsZh?: string[];
  // 状态
  distilledAt?: string;     // 提炼完成时间
  // ... 其他 meta
}
```

## 关键设计决策

| 决策 | 选 | 理由 |
|------|----|----|
| 桌面壳 | Tauri 2 | 小、快、原生；安装包 < 30MB |
| 前端 | React + TS + Vite | 生态最熟 |
| UI 库 | shadcn 风格（手写） | 可定制、零运行时 |
| 状态 | Zustand | 轻量、TS 友好 |
| 数据获取 | TanStack Query | cache / refetch 完备 |
| 后端 | Python 3.11+ FastAPI | AI 生态最强 |
| 包管理 | uv | 极快、取代 pip/poetry |
| LLM 抽象 | litellm | 一行切各家（DeepSeek / OpenAI / Ollama…） |
| 存储 | SQLite (aiosqlite) | 本地优先、零部署 |
| 抓取 | httpx + feedparser | 异步、RSS 库成熟 |
| 调度 | APScheduler | 进程内 cron，够用；不引入 arq 等重组件 |
| 密钥 | 本地加密文件 keystore（AES-256-GCM）| 不入 git、不明文，无 OS prompt |
| 跨进程 | HTTP loopback | 简单、可调试 |
| 双语存储 | 显式 `*_en` / `*_zh` 字段 | 保留搜索英文能力 + 切换语言可看原文 |

## 目录结构（v0.2b 当前）

```
prism/
├── src/                      # React 前端
│   ├── components/{ui,layout}/
│   ├── pages/                # InboxPage, KnowledgePage, SourcesPage, SettingsPage
│   ├── lib/                  # api.ts, utils.ts, theme.ts, language.ts
│   ├── store/                # Zustand
│   ├── i18n/                 # en.json, zh.json
│   ├── styles/               # globals.css
│   ├── types/                # 共享 TS 类型
│   ├── App.tsx, main.tsx
│   └── __tests__/            # Vitest
├── src-tauri/                # Tauri 2 Rust 端
│   ├── src/
│   │   ├── main.rs           # 入口
│   │   ├── lib.rs            # Builder + RunEvent + 启动期一次 keychain→keystore 迁移
│   │   ├── sidecar.rs        # Python 进程管理 + env 注入
│   │   ├── secrets.rs        # 公开 IPC 命令 + 公开 helper 签名（薄封装，转发到 keystore）
│   │   └── keystore.rs       # 本地 AES-256-GCM 加密文件存储 + 一次 keychain→keystore 迁移
│   ├── capabilities/         # 权限（v0.2b 起不再含 keyring）
│   ├── tests/keystore_smoke.rs  # 8 case：roundtrip / 0600 / 损坏容错 / 并发 / key_last4 / 迁移幂等 / 真 Keychain migration
│   └── tests/llm_config_smoke.rs # 9 case：纯 helper + IPC serde camelCase 契约
├── python/                   # Python sidecar
│   ├── pyproject.toml
│   ├── pytest.ini
│   ├── prism_sidecar/
│   │   ├── __main__.py       # CLI 入口
│   │   ├── app.py            # FastAPI 路由
│   │   ├── models.py         # Pydantic v2
│   │   ├── db.py             # aiosqlite + schema migration（v0.2a 新增）
│   │   ├── store.py          # SQLite-backed CRUD
│   │   ├── scheduler.py      # APScheduler 集成（v0.2a 新增）
│   │   ├── config.py         # env 读取
│   │   ├── fetchers/         # 多源抓取（v0.2a 新增）
│   │   │   ├── base.py       # Fetcher Protocol + RawItem
│   │   │   ├── rss.py        # feedparser + httpx
│   │   │   ├── hackernews.py # Algolia API
│   │   │   └── registry.py   # kind → Fetcher 映射
│   │   ├── distillers/       # LLM 提炼（v0.2a 新增）
│   │   │   ├── base.py       # Distiller Protocol + DistilledItem
│   │   │   └── deepseek.py   # litellm 抽象
│   │   ├── pipeline/         # 同步编排（v0.2a 新增）
│   │   │   └── sync.py       # run_source_sync
│   │   └── data/fixtures.py  # 5 个种子源
│   └── tests/                # pytest 38 个 case
├── docs/                     # ROADMAP, ARCHITECTURE
├── scripts/                  # smoke.sh / smoke.ps1
├── assets/                   # logo / icons
├── BRAND.md, AGENTS.md, README.md
└── package.json
```

## 数据 Schema

```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,            -- SourceKind enum
  url TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT,              -- 源特定配置
  last_synced_at TEXT,           -- ISO8601
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE items (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,      -- 用于去重
  title_en TEXT NOT NULL,
  title_zh TEXT,
  summary_en TEXT,
  summary_zh TEXT,
  key_points_zh TEXT,            -- JSON array
  tags_zh TEXT,                  -- JSON array
  author TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  distilled_at TEXT,
  status TEXT NOT NULL DEFAULT 'unread',
  content_type TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
CREATE INDEX idx_items_source ON items(source_id);
CREATE INDEX idx_items_published ON items(published_at DESC);
CREATE INDEX idx_items_status ON items(status);

CREATE TABLE sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  items_new INTEGER DEFAULT 0,
  items_distilled INTEGER DEFAULT 0,
  error TEXT
);

CREATE TABLE sync_jobs (...);    -- job_id → 状态跟踪
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT);  -- 迁移版本
```

**数据文件位置**：`~/.prism/data.db`（可用 `PRISM_DATA_DIR` env 覆盖）。

## 安全模型

- **本地优先**：所有数据存本地 SQLite
- **loopback only**：Python sidecar 只听 `127.0.0.1`，不暴露公网
- **Tauri capabilities**：webview 默认不能调系统命令，必须显式 allow
- **CSP**：dev 阶段关掉，prod 阶段收紧（v0.4）
- **Secrets**：API key 存本地加密文件 `~/.prism/keystore.json`（v0.2b 起替代 OS keychain，根除 macOS 启动期授权弹窗；**前端永远拿不到 key 值**，只能查 `{configured: bool}`）
- **CORS allowlist**：Tauri + Vite origins only
- **sidecar env 注入**：Tauri 启动时从 keystore 读 key → `cmd.env("DEEPSEEK_API_KEY", key)` 注入子进程；key 不会出现在 settings 配置文件里
- **一次 keychain→keystore 迁移**：v0.2b 在 `lib.rs` setup 钩子里调 `keystore::migrate_from_keychain_if_needed` —— v0.2a 及更早用户升上来时第一次会触发一次 macOS 授权弹窗并把 keychain 里的 key 转写到 keystore；之后 `delete_credential` 清掉 keychain entry，**永久免弹窗**

## 性能预算

- 启动到首屏：< 1.5s（冷启动）
- 单次 sync 100 条：< 30s（取决于 LLM）
- 单条 search 查询：< 200ms
- 内存占用：< 300MB idle（vs Electron 500MB+）
- 安装包大小：< 30MB（vs Electron 150MB+）

## 测试覆盖（v0.2b）

| 层级 | 工具 | 覆盖 |
|---|---|---|
| Python fetcher/distiller/store/sync/api | pytest **114** case | rss 5 / hn 3 / distiller 8 / store 8 / sync 5 / api 9 / **FTS5 14** / **cancel 3** / smart-quote 解析等 |
| Rust keystore | `cargo test --test keystore_smoke` | 8 case（roundtrip+密文无明文 / 0600 / 损坏容错 / 并发写 / key_last4 / active-provider 校验 / 迁移幂等 / 真 macOS Keychain migration roundtrip） |
| Rust 公开 helper + IPC serde | `cargo test --test llm_config_smoke` | 9 case（username 格式 / is_known_provider / default_model / CustomLlmConfig JSON 形状 / IPC camelCase） |
| React 关键组件 | Vitest **32** case | Button / InboxPage Sync 按钮 / SourcesPage Add Source dialog / **inline-markdown 10** / **InboxPage 改写 8** / Settings 改写 / Progress hook 等 |
| 端到端 | `bash scripts/smoke.sh` | 启动 sidecar → sync → 验 items |

v0.2c 起加 Playwright E2E（开 Tauri 窗口跑真实交互）。
