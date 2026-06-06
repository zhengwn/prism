# Prism

> **Refract the noise. Illuminate the signal.**
> 把 AI 世界，折射给你。

Prism 是一个 **AI 资讯聚合 + 知识提炼** 桌面应用，把分散在 RSS、YouTube、博客、播客等地方的内容，折射成可检索、可被 Agent 调用的结构化中文知识单元。

跨平台：**Windows** + **macOS**。本地优先，LLM key 走 OS keychain。

---

## 当前状态：v0.2a

**v0.2a 最小可用产品已交付**（2026-06）：SQLite 持久化、HN + RSS 真抓取、DeepSeek 中文提炼、后台调度、Tauri keychain 集成、前端 Sync 按钮 + 双语显示 + Add Source。

下次迭代（v0.2b）：YouTube/X/Podcast/arXiv 等其他源 + 全文搜索。

详细规划见 [`docs/ROADMAP.md`](./docs/ROADMAP.md)。

---

## 核心能力

- 📡 **多源订阅** — HN Algolia、RSS、YouTube（v0.2b）、X（v0.2b）、播客（v0.2b）、博客、PDF、本地文件
- 🧠 **智能提炼** — LLM 把每条素材压成结构化中文知识单元（标题/摘要/关键点/标签），保留原文做双语索引
- 🔌 **Agent 原生**（v0.3）— 暴露 Skill + MCP 接口，让 Claude Code / Cursor / OpenCode 等 Agent 直接读、写、订阅 Prism
- 💻 **本地优先** — 数据全在本地 SQLite，API key 走 OS keychain，不上传云端

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
| 密钥 | **OS keychain (keyring)** | macOS Keychain / Windows Credential Manager |
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

首次启动会自动从 fixtures 导入 5 个种子源（HN + 4 个 RSS），需要**手动配 DeepSeek API key**（Settings → AI 提炼 → Set Key，存到 OS keychain）。

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
│   │   ├── lib.rs            # Builder + keyring plugin + RunEvent
│   │   ├── sidecar.rs        # Python 进程 spawn + env 注入 + 优雅关闭
│   │   └── secrets.rs        # OS keychain 封装（get/set/clear API key）
│   ├── capabilities/         # 权限配置
│   └── icons/                # 应用图标
├── python/                   # Python sidecar
│   ├── pyproject.toml        # uv 管理
│   ├── prism_sidecar/
│   │   ├── __main__.py       # CLI 入口
│   │   ├── app.py            # FastAPI 路由
│   │   ├── models.py         # Pydantic v2（含双语 KnowledgeItem）
│   │   ├── db.py             # aiosqlite + schema migration
│   │   ├── store.py          # SQLite-backed CRUD
│   │   ├── scheduler.py      # APScheduler 集成（每天 9am Asia/Shanghai）
│   │   ├── config.py         # 读 env
│   │   ├── fetchers/         # 多源抓取（base + rss + hackernews + registry）
│   │   ├── distillers/       # LLM 提炼（base + deepseek via litellm）
│   │   ├── pipeline/         # sync 编排
│   │   └── data/fixtures.py  # 5 个种子源
│   └── tests/                # pytest 38 个 case
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
- [ ] **v0.2b** — 多源补齐（YouTube / X / Podcast / arXiv / 全文搜索）
- [ ] **v0.3** — MCP server + Skill bundle（让 Agent 调 Prism）
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

# keychain roundtrip（v0.2a 起的真集成测试）
cd src-tauri && cargo test --test keychain_smoke
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
