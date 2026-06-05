# Prism — Architecture

## 总览

```
┌──────────────────────────────────────────────────────────────┐
│  Tauri Shell (Rust)                                           │
│  ┌──────────────────┐  IPC (Tauri commands)  ┌────────────┐  │
│  │  WebView         │◄──────────────────────►│  Rust Core │  │
│  │  React + TS UI   │                        │  (window,  │  │
│  │                  │                        │   lifecycle│  │
│  └────────┬─────────┘                        │   sidecar) │  │
│           │                                  └─────┬──────┘  │
└───────────┼────────────────────────────────────────┼─────────┘
            │ HTTP (loopback)                       │ std::process::Command
            ▼                                        ▼
   http://127.0.0.1:8765              ┌──────────────────────────┐
            │                         │  Python Sidecar          │
            │                         │  (FastAPI + uvicorn)     │
            ▼                         │  ┌────────────────────┐  │
   /api/sources, /api/items,          │  │  /api/*  REST       │  │
   /api/sync, /health                 │  │  MCP server (stdio)│  │
                                      │  │  LLM pipeline      │  │
                                      │  └────────────────────┘  │
                                      └──────────────────────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────────┐
                                       │  Storage (v0.2)      │
                                       │  - SQLite (aiosqlite)│
                                       │  - sqlite-vec        │
                                       │  - file blobs        │
                                       └──────────────────────┘
```

## 进程边界

- **Tauri 壳** 与 **Python sidecar** 是两个独立进程，通过 `127.0.0.1:8765` HTTP 通信
- 优点：
  - 调试方便（curl / Postman / Vite dev 都能直接打）
  - Tauri 不会因为 Python 崩溃而崩
  - Python 端可以独立升级（不重新打包 Tauri）
- 缺点：
  - 多一个进程
  - 跨平台打包需要打两个东西

## 数据流

### 抓取 + 提炼（v0.2 之后）

```
[Source URL] → fetcher (httpx/yt-dlp) → raw content
            → distiller (litellm) → KnowledgeItem
            → store (aiosqlite) → [queryable]
```

### Agent 调用（v0.3）

```
[Claude Code / Cursor] → MCP client (stdio) → prism mcp server
                                            → [search, read, subscribe tools]
                                            → returns KnowledgeItem
```

## 关键设计决策

| 决策 | 选 | 理由 |
|------|----|----|
| 桌面壳 | Tauri 2 | 小、快、原生 |
| 前端 | React + TS + Vite | 生态最熟 |
| UI 库 | shadcn 风格（手写）| 可定制、零运行时 |
| 状态 | Zustand | 轻量、TS 友好 |
| 数据获取 | TanStack Query | cache / refetch 完备 |
| 后端 | Python 3.11+ FastAPI | AI 生态最强 |
| 包管理 | uv | 极快、取代 pip/poetry |
| LLM 抽象 | litellm | 一行切各家 |
| 存储 | SQLite + sqlite-vec | 本地优先、零部署 |
| 跨进程 | HTTP loopback | 简单、可调试 |

## 目录结构

```
prism/
├── src/                       # React 前端
│   ├── components/
│   │   ├── ui/                # shadcn 风格基础组件
│   │   └── layout/            # 三栏布局
│   ├── pages/                 # 路由页面
│   ├── lib/                   # utils / api client
│   ├── store/                 # Zustand
│   ├── styles/                # 全局样式
│   ├── types/                 # 共享类型
│   ├── App.tsx
│   └── main.tsx
├── src-tauri/                 # Tauri Rust 端
│   ├── src/
│   │   ├── main.rs            # 入口
│   │   ├── lib.rs             # Builder
│   │   └── sidecar.rs         # Python 进程管理
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── capabilities/          # 权限配置
│   └── icons/                 # 应用图标
├── python/                    # Python sidecar
│   ├── pyproject.toml
│   ├── prism_sidecar/
│   │   ├── __main__.py        # 入口
│   │   ├── app.py             # FastAPI app
│   │   ├── models.py          # Pydantic
│   │   ├── store.py           # 数据层
│   │   └── data/fixtures.py   # v0.1 假数据
│   └── README.md
├── docs/                      # 设计文档
│   ├── ROADMAP.md
│   └── ARCHITECTURE.md
├── scripts/                   # 仓库脚本
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
├── BRAND.md                   # 品牌指南
├── README.md
└── .gitignore
```

## 安全模型

- **本地优先**：所有数据存本地 SQLite（v0.2）
- **loopback only**：Python sidecar 只听 `127.0.0.1`，不暴露公网
- **Tauri capabilities**：webview 默认不能调系统命令，必须显式 allow
- **CSP**：dev 阶段关掉，prod 阶段收紧（v0.4）
- **Secrets**：API key 存 OS keychain，不入 git（v0.2）

## 性能预算

- 启动到首屏：< 1.5s（冷启动）
- 单次 sync 100 条：< 30s（取决于 LLM）
- 单条 search 查询：< 200ms
- 内存占用：< 300MB idle（vs Electron 500MB+）
- 安装包大小：< 30MB（vs Electron 150MB+）
