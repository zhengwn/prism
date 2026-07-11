# Prism

> **Refract the noise. Illuminate the signal.**
> 把 AI 世界，折射给你。

Prism 是一个 **AI 资讯聚合 + 知识提炼** 桌面应用，把分散在 RSS、YouTube、博客、播客等地方的内容，折射成可检索、可被 Agent 调用的结构化中文知识单元。

跨平台：**Windows** + **macOS**。本地优先，LLM key 走本地加密文件。

---

## 当前状态：v0.2c 已完成，v0.3 进行中

**v0.2b 已交付**（2026-06-10）：v0.2a 之后的一轮基础设施重构 + UX 打磨——

- 密钥体系重写：Tauri 端用本地 AES-256-GCM keystore 替换 `tauri-plugin-keyring`，根除 macOS 启动期 keychain 授权弹窗
- LLM 提炼：5 个 provider 精简为 2 个（DeepSeek + MiniMax）
- 提炼体验：实时蒸馏进度（SSE 流）+ 取消按钮 + 进度条 / 取消 toast
- 同步体验：sync 异步化 + 跑太久可中途取消（per-source 边界）
- 搜索：InboxPage 走 SQLite FTS5 全文搜索（中文 prefix match + sanitizer 兜底）
- 详情页：编号 / `#标签` / `**重点**` inline-markdown 渲染

**v0.2c 已完成**（2026-07-10 本机全量验证）：Bilibili fetcher 作为 PoC 合入 main（mid/bvid 两种模式 + CC/AI 字幕合并 + 章节切分 + Bilibili 专属提炼 prompt，前端 SourcesPage/DetailPanel 已接入）。本轮新增：**YouTube fetcher**（yt-dlp，channel/video 两模式 + 字幕四级优先 + 共享 `[CC]/[AI]` 字幕格式复用视频提炼 prompt，前端播放器/badge/i18n 已接入）和**错误重试 + 速率限制**（`FetchError` 契约让整源失败真正落到 `sources.last_error`；`retry_async` + per-host `HostThrottle`；失败冷却 min(2^n,24)h + 每小时补跑 job）——设计稿见 `docs/design/`。本轮再加：**Podcast fetcher**（继承 RSSFetcher，enclosure/itunes:duration）、**arXiv fetcher**（新增 arxiv kind，categories 配置 + 3s API 限速）、**优雅关闭**（SIGTERM → in-flight job 在 per-source 检查点落盘部分进度再退，Tauri 5s 宽限）、**Vite 下 setApiKey 报错修复**。sidecar 内部也做了一轮拆分：同步 job 的并发控制/取消逻辑从 `app.py` 挪到了独立的 `pipeline/orchestrator.py`，路由文件瘦身到 500 多行。收尾一轮再加：**X fetcher（bridge-RSS PoC）**（继承 RSSFetcher，指向自托管 RSSHub/Nitter feed，handle 解析 + tweet 元数据 + X 短文本专属 prompt）、**Apply & Restart Sidecar 按钮**（`restart_sidecar` command，改 key 后不必重启整个 app）、**Playwright 前端 E2E**（hermetic mock sidecar，5 个 smoke case；Tauri-shell 层留待 WebdriverIO + `@wdio/tauri-service`，macOS 无原版 tauri-driver）。

测试（**本机实跑，非静态计数**）：pytest **305/305**（v0.2c 254 + v0.3 MCP 读 13 + 写/webhook 38）· vitest **28/28** · Rust **17/17**（keystore_smoke 8 + llm_config_smoke 9）· Playwright E2E **5/5** · `cargo check --all-targets` + `npm run build` 干净。复核：`cd python && uv run pytest -v` / `npm test` / `cargo test` / `npm run test:e2e`。

收尾验证时修掉了三个真实缺陷（浮点 jitter 断言、`llm_config_smoke.rs` 自 v0.2b 起编译不过、vitest 误收 Playwright spec），并真跑了 sidecar 端到端（真实 RSS 30 条 / arXiv 50 条、`FetchError` → `last_error` + 24h 冷却、SIGTERM 中断 sync 3.87s 内落盘部分进度）。详见 `docs/ROADMAP.md` 的「v0.2c 收尾验证」。

v0.3 进行中：**MCP server 已落地**（`prism-mcp`，stdio 九工具——读 4 + 写 subscribe/set_enabled + webhook 注册 3，见下面「Agent 接入」）+ **Webhook 推送**（新条目按源/标签匹配后 sidecar HMAC 签名 POST）+ **Claude Code Skill bundle**（`skills/prism-knowledge-base/`）。下一步 Skill 的 OpenCode/Mavis manifest。

详细规划见 [`docs/ROADMAP.md`](./docs/ROADMAP.md)。

---

## 核心能力

- 📡 **多源订阅** — HN Algolia、RSS、博客、Podcast、arXiv、Bilibili、YouTube、X（v0.2c，X 为 bridge-RSS PoC，需自托管 RSSHub/Nitter；PDF / 本地文件待做）
- 🧠 **智能提炼** — LLM 把每条素材压成结构化中文知识单元（标题/摘要/关键点/标签），保留原文做双语索引
- 🔍 **全文搜索** — SQLite FTS5，中文 prefix match，~5ms 命中（v0.2b）
- ⚡ **实时进度 + 可取消** — 蒸馏 / 同步跑太久可中途取消，UI 不卡（v0.2b）
- 🔌 **Agent 原生**（v0.3）— 暴露 Skill + MCP 接口，让 Claude Code / Cursor / OpenCode 等 Agent 直接读、写、订阅 Prism
- 💻 **本地优先** — 数据全在本地 SQLite，API key 走本地加密 keystore（`~/.prism/keystore.json`，AES-256-GCM），不上传云端

---

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 桌面壳 | **Tauri 2** | 小、快、原生；安装包 < 30MB |
| 前端 | **React 18 + TypeScript + Vite** | 生态最熟，TanStack Query 完备 |
| UI | **shadcn 风格（手写）** + **Tailwind** | 可定制、零运行时、token 化主题 |
| 状态 | **Zustand** | 轻量、TS 友好 |
| 后端 | **Python 3.11+ FastAPI + uvicorn** | AI 生态最强 |
| 存储 | **SQLite (aiosqlite)** | 本地优先、零部署 |
| 抓取 | **httpx + feedparser** | 异步 + 成熟 RSS 库 |
| 提炼 | **litellm** | 一行切各家 LLM（默认 DeepSeek） |
| 调度 | **APScheduler** | 进程内 cron，简单够用 |
| 密钥 | **本地 AES-256-GCM keystore** | `~/.prism/keystore.json`，0600 perms，macOS 无启动期 keychain 弹窗（v0.2b） |
| 包管理 | **uv** | 极快，取代 pip/poetry |

---

## Quick Start

```bash
# 1. JS 依赖
npm install

# 2. Python 依赖
cd python && uv sync && cd ..

# 3. 开发模式（Tauri 窗口 + Vite + 自动 spawn sidecar）
npm run tauri:dev

# 4. 单独验证 sidecar（不开 Tauri）
npm run sidecar:dev       # 启动
bash scripts/smoke.sh     # 端到端：健康检查 + sync + items 列表
```

Dev 模式下：
- Tauri 窗口（Tauri webview + React UI）
- Vite dev server: `http://localhost:1420`
- Python sidecar: `http://127.0.0.1:8765`

首次启动会自动从 fixtures 导入 8 个种子源（HN + 3 个 Bilibili PoC UP 主 + 4 个 RSS），需要**手动配 DeepSeek API key**（Settings → AI 提炼 → Set Key，存到本地 `~/.prism/keystore.json` 加密文件）。

### Agent 接入（MCP，v0.3）

Prism 的本地知识库可以直接喂给 Agent——stdio 模式的 MCP server，**app 不用在跑**：

```bash
claude mcp add prism -- uv --directory /path/to/prism/python run prism-mcp
```

九个工具：读——`prism_search`（FTS5 双语全文搜索）/ `prism_recent_items` / `prism_get_item` / `prism_list_sources`；写——`prism_subscribe`（加源，带早期配置校验）/ `prism_set_source_enabled`；webhook——`prism_register_webhook`（新条目按源/标签匹配后 HMAC 签名 POST 给你的服务）/ `prism_list_webhooks` / `prism_set_webhook_enabled`。**刻意无删除**，停用即可（删源会 cascade 删所有条目）。详见 `python/README.md`。

想让 Agent 用好这些工具，附带一个 **Claude Code Skill**（`skills/prism-knowledge-base/`）——`cp -r skills/prism-knowledge-base ~/.claude/skills/` 即可，教 Agent 何时用哪个工具、怎么用中文总结。

---

## 项目结构

```
prism/
├── src/                      # React + TS 前端
│   ├── components/
│   │   ├── ui/               # shadcn 风格基础组件
│   │   └── layout/           # 三栏布局
│   ├── pages/                # InboxPage, KnowledgePage, SourcesPage, SettingsPage
│   ├── lib/                  # api.ts (sidecar 客户端), utils.ts, theme.ts, language.ts
│   ├── store/                # Zustand 全局状态
│   ├── i18n/                 # en.json / zh.json
│   ├── styles/               # globals.css (Tailwind + CSS 变量)
│   ├── types/                # 共享 TS 类型（与 Python models 同步）
│   ├── App.tsx               # 路由
│   └── main.tsx              # 入口
├── src-tauri/                # Tauri 2 Rust 壳
│   ├── src/
│   │   ├── main.rs           # 入口
│   │   ├── lib.rs            # Builder + keystore + RunEvent
│   │   ├── sidecar.rs        # Python 进程 spawn + env 注入 + 进程树 kill（仍是硬杀，非优雅关闭，见 ROADMAP v0.2c）
│   │   ├── secrets.rs        # 本地 keystore 封装（get/set/clear API key）
│   │   └── keystore.rs       # AES-256-GCM 加解密 + keychain 一次性迁移（v0.2b）
│   ├── capabilities/         # 权限配置
│   └── icons/                # 应用图标
├── python/                   # Python sidecar
│   ├── pyproject.toml        # uv 管理
│   ├── prism_sidecar/
│   │   ├── __main__.py       # CLI 入口
│   │   ├── app.py            # FastAPI 路由（含 /api/sync/{jobId}/cancel）
│   │   ├── models.py         # Pydantic v2（含双语 KnowledgeItem）
│   │   ├── db.py             # aiosqlite + schema migration（含 FTS5 v2）
│   │   ├── fts5.py           # SQLite FTS5 全文搜索（v0.2b）
│   │   ├── progress.py       # 提炼进度内存 store（v0.2b）
│   │   ├── settings.py       # active provider R/W
│   │   ├── store.py          # SQLite-backed CRUD（含 v0.3 webhooks）
│   │   ├── mcp_server.py     # MCP server（stdio，v0.3；prism-mcp 入口；读 4 + 写 2 + webhook 3 工具）
│   │   ├── webhooks.py       # webhook 投递 + HMAC 签名 + SSRF 守卫（v0.3）
│   │   ├── scheduler.py      # APScheduler 集成（每天 9am Asia/Shanghai）
│   │   ├── config.py         # 读 env
│   │   ├── fetchers/         # 多源抓取（base + rss + hackernews + bilibili PoC + registry）
│   │   ├── distillers/       # LLM 提炼（base + deepseek + minimax + bilibili_prompt + registry）
│   │   ├── pipeline/         # sync.py（单源）+ orchestrator.py（job 编排/取消）+ distill.py（重蒸馏批处理）
│   │   └── data/fixtures.py  # 8 个种子源（HN + 3 个 Bilibili PoC + 4 个 RSS）
│   └── tests/                # pytest 305 个 case（实跑核对；含 bilibili/youtube/arxiv/x fetcher、retry、fetcher registry、mcp server）
├── docs/                     # 设计文档
│   ├── ROADMAP.md
│   └── ARCHITECTURE.md
├── scripts/                  # smoke.sh / smoke.ps1 / run-sidecar.sh
├── assets/                   # logo / icons / screenshots
├── BRAND.md                  # 品牌指南
├── AGENTS.md                 # 给 AI agent 团队的工程规范
└── README.md                 # 你正在看的
```

---

## 路线

- [x] **v0.1** — Hello Prism（Tauri + React + Python 假数据）
- [x] **v0.2a** — 最小可用：SQLite + 真抓取 + DeepSeek 提炼 + 调度
- [x] **v0.2b** — 基础设施重构 + UX 打磨（本地 keystore / 2 provider / 实时进度 / 可取消 / FTS5 / 详情 markdown）
- [x] **v0.2c** — 多源补齐 + 错误处理：七路 fetcher（RSS/HN/Bilibili/YouTube/Podcast/arXiv/X）+ 重试/限速/冷却 + 优雅关闭 + Playwright E2E，全部本机实测
- [ ] **v0.3**（进行中）— Agent 接口：MCP server（stdio 九工具，含 subscribe/webhook 写）+ Webhook 推送 + Claude Code Skill bundle 已落地；剩 Skill 的 OpenCode/Mavis manifest
- [ ] **v0.4** — 跨平台打包（Windows MSI / macOS DMG / sidecar 打包）
- [ ] **v0.5** — UX 完善（标签 / ⌘K / 通知）
- [ ] **v1.0** — 公开发布

---

## 开发

```bash
# 类型检查
npx tsc -b

# 前端生产构建
npx vite build

# Rust 检查
cd src-tauri && cargo check && cd ..

# 端到端冒烟（启动 sidecar + sync + 验 items）
npm run smoke

# Python 测试
cd python && uv run pytest -v

# keystore roundtrip（v0.2b 起的真集成测试，含 keychain 一次性迁移）
cd src-tauri && cargo test --test keystore_smoke

# LLM config helper + IPC serde 契约（9 case）
cd src-tauri && cargo test --test llm_config_smoke
```

详细工程规范（i18n 强制、theme tokens、commit 规范、安全模型等）见 [`AGENTS.md`](./AGENTS.md)。

---

## 品牌 & 文案

- 完整品牌指南：[`BRAND.md`](./BRAND.md)
- 主 slogan：`Refract the noise. Illuminate the signal.`
- 中文 slogan：`把 AI 世界，折射给你`

---

## License

TBD
