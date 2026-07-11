# Prism — Architecture

> 截至 v0.2c 完成（2026-07-10，本机全量验证）：v0.2a + v0.2b 基础设施重构 + UX 打磨之后完成多源补齐——RSS / HN / Bilibili / YouTube / Podcast / arXiv / X（bridge-RSS PoC）七路 fetcher + 错误重试与速率限制 + 优雅关闭。v0.3 之后会加 MCP server、sqlite-vec 语义搜索、Skill bundle 等。

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
                              │   /api/distill/* (SSE)   │
                              │   /api/settings/*        │
                              │                          │
                              │  Pipeline:               │
                              │   fetchers/ (rss+hn+     │
                              │     bilibili+youtube+    │
                              │     podcast+arxiv+x)     │
                              │   distillers/ (deepseek+ │
                              │     minimax)             │
                              │   pipeline/sync+         │
                              │     orchestrator+distill │
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
              ├─ RSSFetcher:        feedparser + httpx, retry/backoff, HTML strip
              ├─ HackerNewsFetcher: Algolia API，多关键词池去重
              └─ BilibiliFetcher (PoC, v0.2c): mid/bvid 两种模式 + CC/AI 字幕合并
                ↓
            pipeline/sync.py: run_source_sync(source)
              ├─ 遍历 RawItem
              ├─ items.url unique → skip 已存在
              ├─ insert raw item (distilled_at=NULL)
              └─ if distiller configured (registry: deepseek | minimax):
                   distiller.distill(raw)
                     ├─ litellm.acompletion → JSON {title_zh, summary_zh, key_points_zh, tags_zh}
                     │   （Bilibili 源走 distillers/bilibili_prompt.py 的专属 prompt：章节切分 + CC/AI 合并）
                     ├─ parse + validate（含全角引号等 JSON 修复兜底，见 distillers/base.py）
                     └─ update_item(item.id, distilled)
                ↓
            SQLite (sources, items, sync_log)
                ↓
            [GET /api/items?source_id=&status=&q=&limit=&offset=] → React UI
```

`pipeline/sync.py` 只负责单源的 fetch+dedupe+distill；job 级别的并发控制、取消、跨源编排在 `pipeline/orchestrator.py`（v0.2c 从 `app.py` 拆出，见下）。

### 手动 vs 自动同步

- **手动**：`POST /api/sync`（全源）或 `POST /api/sync/{source_id}`（单源）→ 返回 `SyncResult`
  - 并发保护：`pipeline/orchestrator.py` 的 in-memory `inflight_jobs` set，第二次请求返回 409 Conflict
  - `app.py` 里的 `_inflight_jobs` 是同一个 set 对象的向后兼容别名（`tests/test_api.py` 直接戳这个名字）
- **自动**：APScheduler `AsyncIOScheduler` 在 FastAPI lifespan 启动
  - `cron(hour=9, timezone="Asia/Shanghai")` 每天 9 点跑 `orchestrator.run_all_sync_background()`（`scheduler.py` 通过 `app.py` 的向后兼容别名懒加载 import，避免循环依赖）
  - 单源失败不影响其他源（每个源独立 `try/except` + 写 `sync_log.error`）
  - 同一时刻全进程只跑一个 job（不区分手动/定时、不区分源）——现阶段源数量不多，这是刻意的简化，不是 bug；`orchestrator.py` 文件头注释里记录了这个取舍

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

### Distill

| Method | Path | Returns | 备注 |
|---|---|---|---|
| GET | `/api/distill/pending-count` | `{pending: int}` | 待提炼数量（`distilled_at IS NULL` 计数） |
| GET | `/api/distill/status` | dict | 整体提炼进度快照（`isRunning`/`pending`/`distilled`/`failed`/`currentTitle`/…） |
| GET | `/api/distill/status/stream` | `text/event-stream` (SSE) | 实时进度推送，前端 `useDistillProgress` hook 订阅 |
| POST | `/api/distill/redistill?batch_limit=` | `RedistillResponse` | 重跑所有 `distilled_at IS NULL` 的条目；`key_invalid=true` 时提前停批 |

**没有独立的 `/api/distill/cancel` 端点** —— 取消是在 sync job 层面做的（`POST /api/sync/{job_id}/cancel`），`redistill` 批处理目前跑起来后无法从外部中途取消，只会在 key 失效时自己提前停。之前这份文档写过一个 `/api/distill/cancel`，代码里从来没有这个路由，已经改掉。

### Settings

| Method | Path | Returns | 备注 |
|---|---|---|---|
| GET | `/api/settings/providers` | `ProviderSchema[]` | 每个 provider 的 Settings-UI 元数据（当前 2 个：deepseek/minimax） |
| GET | `/api/settings/llm` | `LlmConfig` | 当前 active provider（不含 key） |
| POST | `/api/settings/llm` | `LlmConfig` | 切换 active provider；body 带 `apiKey` 会被 400 拒绝——key 只走 Tauri keystore + env 注入，不经过 sidecar HTTP |

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

## 目录结构（v0.2c 进行中，当前）

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
│   │   ├── sidecar.rs        # Python 进程管理 + env 注入 + 进程树 kill（uv 派生的 python 子进程也一起清）
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
│   │   ├── app.py            # FastAPI 路由（薄——同步编排在 pipeline/orchestrator.py）
│   │   ├── models.py         # Pydantic v2
│   │   ├── db.py             # aiosqlite + schema migration（v2 加 FTS5）
│   │   ├── fts5.py           # SQLite FTS5 全文搜索
│   │   ├── progress.py       # 提炼进度内存 store（喂给 SSE 流）
│   │   ├── settings.py       # PROVIDER_SCHEMAS + active_provider.json R/W
│   │   ├── store.py          # SQLite-backed CRUD
│   │   ├── mcp_server.py     # 只读 MCP server（stdio，v0.3；复用 init_db + store 读）
│   │   ├── scheduler.py      # APScheduler 集成
│   │   ├── config.py         # env 读取
│   │   ├── fetchers/         # 多源抓取
│   │   │   ├── base.py       # Fetcher Protocol + RawItem
│   │   │   ├── rss.py        # feedparser + httpx
│   │   │   ├── hackernews.py # Algolia API
│   │   │   ├── bilibili.py   # mid/bvid + CC/AI 字幕合并（v0.2c PoC）
│   │   │   └── registry.py   # kind → Fetcher 映射
│   │   ├── distillers/       # LLM 提炼
│   │   │   ├── base.py             # Distiller Protocol + DistilledItem + 共享 prompt/重试/JSON 修复
│   │   │   ├── deepseek.py         # litellm 抽象
│   │   │   ├── minimax.py          # litellm 抽象（OpenAI 兼容协议）
│   │   │   ├── bilibili_prompt.py  # Bilibili 专属 prompt（章节切分 + CC/AI 合并）
│   │   │   └── registry.py         # provider id → Distiller 映射
│   │   ├── pipeline/         # 同步编排
│   │   │   ├── sync.py         # run_source_sync（单源 fetch+dedupe+distill）
│   │   │   ├── orchestrator.py # job 编排：并发控制/取消/后台任务（从 app.py 拆出）
│   │   │   └── distill.py      # redistill 批处理
│   │   └── data/fixtures.py  # 8 个种子源（HN + 3 个 Bilibili PoC + 4 个 RSS）
│   └── tests/                # pytest 267 个 case（实跑核对）
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
  duration_sec INTEGER,          -- 视频/音频时长（Bilibili、未来 podcast/YouTube 用）
  metadata_json TEXT,
  FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
CREATE INDEX idx_items_source ON items(source_id);
CREATE INDEX idx_items_published ON items(published_at DESC);
CREATE INDEX idx_items_status ON items(status);

CREATE TABLE sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  job_id TEXT,                   -- 关联 sync_jobs.job_id（v0.2b 加，之前这份文档漏了这一列）
  started_at TEXT NOT NULL,
  finished_at TEXT,
  items_new INTEGER DEFAULT 0,
  items_distilled INTEGER DEFAULT 0,
  error TEXT
);

CREATE TABLE sync_jobs (...);    -- job_id → 状态跟踪
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT);  -- schema_version 等迁移元数据

-- schema v2（已上线，不是"未来计划"）：FTS5 虚拟表 + 触发器，随 items 增删改自动同步。
-- 中文分词用 unicode61（不带 remove_diacritics，否则中文单字前缀搜索会坏）。
CREATE VIRTUAL TABLE items_fts USING fts5(
  title_en, title_zh, summary_en, summary_zh, key_points_zh, tags_zh,
  content='items', content_rowid='rowid', tokenize='unicode61'
);
-- + items_ai / items_ad / items_au 三个 AFTER INSERT/DELETE/UPDATE 触发器，见 db.py
```

**数据文件位置**：`~/.prism/data.db`（可用 `PRISM_DATA_DIR` env 覆盖）。sqlite-vec（语义搜索）仍留到 v0.5，还没做。

## 安全模型

- **本地优先**：所有数据存本地 SQLite
- **loopback only**：Python sidecar 只听 `127.0.0.1`，不暴露公网
- **Tauri capabilities**：webview 默认不能调系统命令，必须显式 allow
- **CSP**：dev 阶段关掉，prod 阶段收紧（v0.4）
- **Secrets**：API key 存本地加密文件 `~/.prism/keystore.json`（v0.2b 起替代 OS keychain，根除 macOS 启动期授权弹窗；默认读路径**只能查** `{configured: bool}` / `keyLast4`，拿不到明文 key；唯一例外是 `reveal_llm_key`——Settings 页"眼睛"按钮在用户主动点击时才会调用，把明文 key 短暂交给渲染进程，这是有意为之的例外，见 `secrets.rs` 里 `reveal_llm_key` 的 SECURITY 注释）
- **CORS allowlist**：Tauri + Vite origins only
- **sidecar env 注入**：Tauri 启动时从 keystore 读 active provider 的 key，按 provider 注入对应 env var（`DEEPSEEK_API_KEY` 或 `MINIMAX_API_KEY` + `MINIMAX_API_BASE`）；key 不会出现在 settings 配置文件里
- **一次 keychain→keystore 迁移**：v0.2b 在 `lib.rs` setup 钩子里调 `keystore::migrate_from_keychain_if_needed` —— v0.2a 及更早用户升上来时第一次会触发一次 macOS 授权弹窗并把 keychain 里的 key 转写到 keystore；之后 `delete_credential` 清掉 keychain entry，**永久免弹窗**

## 性能预算

- 启动到首屏：< 1.5s（冷启动）
- 单次 sync 100 条：< 30s（取决于 LLM）
- 单条 search 查询：< 200ms
- 内存占用：< 300MB idle（vs Electron 500MB+）
- 安装包大小：< 30MB（vs Electron 150MB+）

## 测试覆盖（v0.2c 已完成）

> 下面的 case 数是 2026-07-10 在本机**现场跑出来**的（Python 侧另用 `pytest --collect-only` 逐文件核对）。此前这张表写的是静态数 `def test_` 得出的估计值，和 AGENTS.md 里的数字长期互相矛盾（175 vs 222）；实跑 v0.2c 收尾时是 254，v0.3 加上 MCP server 的 13 个后为 267。

| 层级 | 工具 | 覆盖 |
|---|---|---|
| Python fetcher/distiller/store/sync/api/mcp | `cd python && uv run pytest -v` — **267/267 绿** | bilibili prompt 31 / **x fetcher 23** / deepseek distiller 22 / bilibili fetcher 18 / FTS5 17 / **youtube fetcher 16** / settings api 20 / **retry+throttle 13** / **mcp server 13**（v0.3）/ sync 12 / api 12 / provider registry 11 / minimax distiller 11 / **fetcher registry 11**（含 `lookback_days` 签名回归）/ store 9 / **arxiv fetcher 8** / rss 7 / distill 5 / **podcast fetcher 4** / hn 4 |
| Rust keystore | `cargo test --test keystore_smoke` | **8/8 绿**（roundtrip+密文无明文 / 0600 / 损坏容错 / 并发写 / key_last4 / active-provider 校验 / 迁移幂等 / 真 macOS Keychain migration roundtrip） |
| Rust 公开 helper + IPC serde | `cargo test --test llm_config_smoke` | **9/9 绿**（username 格式 / is_known_provider / default_model / CustomLlmConfig JSON 形状 / IPC camelCase，含 `keyLast4`/`keyLength`）。注：v0.2b~v0.2c 期间这个 target 一直**编译不过**（结构体加了字段、测试没跟），v0.2c 收尾才修好 |
| React 关键组件 | `npm test` — **28/28 绿** | Button 3 / DetailPanel 4 / inline-markdown 10 / InboxPage 2 / SettingsPage 4 / SourcesPage 5 |
| 前端 E2E（浏览器层） | `npm run test:e2e` — **5/5 绿** | Playwright + hermetic mock sidecar：inbox 渲染 / sync toast / 建 X 源 / settings restart 按钮 / 中文 UI 渲染 `titleZh`。**挂不到 Tauri 原生 webview**，壳内 `invoke`/keystore/sidecar spawn 未覆盖 |
| 端到端 | `bash scripts/smoke.sh` | 启动 sidecar → sync → 验 items。⚠️ 不隔离 `PRISM_DATA_DIR`，会写真实 `~/.prism/data.db` |

v0.2c 已全部落地。真实端到端验证（隔离 DB、真网络）与仍未覆盖的部分（Tauri 壳内路径、真实 distill、B站/YouTube/Podcast 的实网抓取）见 `ROADMAP.md` 的「v0.2c 收尾验证」。
