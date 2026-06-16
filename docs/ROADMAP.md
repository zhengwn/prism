# Prism — Roadmap

> 公开 v1.0 之前的规划。v0.2b 是当前完成点，下一站 v0.2c（多源补齐）。

## 实际状态

- **v0.1** ✅ 完成（2026-05）：Tauri + React + Python 假数据 + 主题 + i18n + smoke
- **v0.2a** ✅ 完成（2026-06）：SQLite + RSS/HN 真抓取 + DeepSeek 中文提炼 + 调度 + Tauri keychain
  - Producer commits: `6d06cd2`（Tauri）、`a265415`（Frontend）、`66c6e11`（Sidecar）
  - 38/38 pytest 绿，端到端 smoke 抓到 113 条 HN 故事
- **v0.2b** ✅ 完成（2026-06-10）：本地 keystore 重构 + UX 打磨 + 全文搜索
  - 范围（跟 ROADMAP 原列的 v0.2b 不一样 —— 实际先做的是基础设施 + 体验补齐，多源留到 v0.2c）：
    - 密钥体系：Tauri 端用本地 AES-256-GCM keystore 替换 `tauri-plugin-keyring`，根除 macOS 启动期 keychain 授权弹窗
    - LLM 提炼：5 个 provider 精简为 2 个（DeepSeek + MiniMax），i18n 同步瘦身
    - 提炼体验：实时蒸馏进度（SSE 流）+ 取消按钮 + 进度条 / 取消 toast
    - 同步体验：sync 异步化 + 跑太久可中途取消（per-source 边界）
    - 搜索：InboxPage 走 SQLite FTS5 全文搜索（中文 prefix match + sanitizer 兜底）
    - 详情页：编号 / #标签 / `**重点**` inline-markdown 渲染
  - 测试：114/114 pytest 绿，32/32 vitest 绿，8/8 Rust keystore smoke 绿
  - 收尾 commit: `8acdf43` (inbox) / `1769463` (sync) / `6239674` (FTS5) / `b8f8ee8` (keystore merge)
- 下一站 **v0.2c**：补齐其他源（YouTube / X / Podcast / arXiv）

## v0.1 — Hello Prism ✅

- [x] Tauri 2 壳工程
- [x] React + TS + Vite 前端骨架
- [x] Python FastAPI sidecar（内存假数据）
- [x] 三栏布局（侧栏 + 内容区 + 详情区）
- [x] 路由（Inbox / Knowledge / Sources / Settings）
- [x] 假数据端到端跑通
- [x] 端到端冒烟测试 (`npm run smoke` / `pwsh scripts/smoke.ps1`)
- [x] 图标（替换占位）
- [x] 暗/亮主题切换
- [x] i18n 英文/中文切换

## v0.2a — 数据层 + RSS + DeepSeek 提炼 ✅

最小可用产品。抓真实内容 + 真实提炼 + 真正能用。

- [x] SQLite 持久化（aiosqlite；sqlite-vec 留到 v0.5 全文搜索时启用）
- [x] 通用 RSS fetcher（feedparser + httpx，含 retry / HTML strip / lookback window）
- [x] Hacker News fetcher（Algolia API，5 个关键词池去重）
- [x] 5 个种子源：HN + Simon Willison + OpenAI + DeepMind + Hugging Face
- [x] DeepSeek 提炼 pipeline（litellm 抽象，标题/摘要/关键点/标签全部翻译为中文）
- [x] 数据层双语存储（`title_en` / `title_zh` / `summary_en` / `summary_zh` / `tags_zh` / `key_points_zh`）
- [x] APScheduler 调度：每天早上 9 点（Asia/Shanghai）自动跑 + 手动 `POST /api/sync` 触发
- [x] SourcesPage Add Source 按钮接通（创建/删除/启用切换，PATCH /api/sources/{id}）
- [x] InboxPage 顶部 Sync now 按钮（idle/running/success/error 状态机 + toast）
- [x] SettingsPage 配置 DeepSeek API key（经 Tauri command → OS keychain）
- [x] Tauri：API key 走 OS keychain，spawn sidecar 时注入 `DEEPSEEK_API_KEY` env
- [x] pytest 38 个 case（rss 5 + hn 3 + distiller 8 + store 8 + sync 5 + api 9）
- [x] Vitest 7 个 case（Button / InboxPage Sync 按钮 / SourcesPage Add Source dialog）

### v0.2a 已知尾巴（留给 v0.2b / v0.5）

- [x] i18n `_keyIndex` 数组：v0.2a 为兼容 verifier 写的临时数组，5KB 死重量，**v0.2b 已清理**
- [x] Vitest 覆盖再加（v0.2b 从 7 case → 32 case，+25）
- [ ] `setApiKey` 在 Vite 调试下抛错（被 React Query onError 接住，prod 无影响，留到 v0.2c 顺手清）

## v0.2b — 基础设施重构 + UX 打磨 ✅

实际交付的 v0.2b 不再是"多源补齐"（那一坨挪到 v0.2c），而是一轮**先打地基**的迭代：把 v0.2a 留下的密钥痛点干掉、把交互体验补到能用的水位、把全文搜索这个高频需求先做了。

- [x] **密钥体系重写**：Tauri 端用本地 AES-256-GCM keystore（`~/.prism/keystore.json` + 0600 master key）替换 `tauri-plugin-keyring`，根除 macOS 启动期 keychain 授权弹窗；前端永远拿不到 key 值，只能查 `{configured: bool}`；active provider 切换走 `sidecar::restart()` 刷新 env（`b8f8ee8`）
- [x] **LLM provider 瘦身**：5 个 → 2 个（DeepSeek + MiniMax），i18n / 测试 / 注册表同步精简（`428e47d`）
- [x] **提炼实时进度**：SSE 流 + 进度条 + 取消按钮 + 取消 toast，蒸馏中改 key 不卡（`51c91a8` / `5a29e1d`）
- [x] **同步可取消**：sync 异步化 + per-source 边界检查点，长跑可中断（`1769463`）
- [x] **全文搜索**：SQLite FTS5 索引 + 前端 search 框，~5ms 命中；含 CJK prefix + FTS5 语法 sanitizer（`6239674` / `8acdf43`）
- [x] **详情页打磨**：编号 / `#标签` / `**重点**` inline-markdown 渲染（`8acdf43`）
- [x] **i18n 死重量清理**：`_keyIndex` 临时数组移除
- [x] **Vitest 覆盖扩展**：7 → 32 case（+ 25 个新 case）
- [x] **测试**：114/114 pytest、32/32 vitest、8/8 Rust keystore smoke

## v0.2c — 多源补齐 + 错误处理（下一站）

- [ ] YouTube fetcher（yt-dlp + 字幕提取）
- [ ] X fetcher（FxTwitter API 或自托管 scraper，先 PoC 选型）
- [ ] Podcast fetcher（RSS 变种，加 enclosure / 时长 / 章节字段）
- [ ] arXiv fetcher（cs.AI / cs.LG / cs.CL，按提交时间排序）
- [ ] 错误重试 + 速率限制（per-source 配额 + APScheduler backoff）
- [ ] Tauri：`Apply & Restart Sidecar` 按钮（避免改 key 必重启 app）
- [ ] Tauri：scheduler 优雅关闭（SIGTERM → 跑完 in-flight 再退）
- [ ] Vite 调试下 `setApiKey` 抛错修复
- [ ] Playwright for Tauri E2E（开 Tauri 窗口跑真实交互）

## v0.3 — Agent 接口

- [ ] MCP server（stdio 模式，让 Claude Code / Cursor / OpenCode 等 Agent 调用）
- [ ] Skill bundle（Mavis / OpenCode / Claude Code 格式）
- [ ] Prism 内知识库的 read / search / subscribe 工具
- [ ] Webhook：外部 Agent 订阅特定标签 / 源

## v0.4 — 跨平台打包

- [ ] Windows 打包（MSI / NSIS）
- [ ] macOS 打包（DMG / universal binary）
- [ ] Python sidecar 打包（PyInstaller / pyoxidizer）
- [ ] 自动更新（tauri-plugin-updater）

## v0.5 — UX 完善

- [ ] 标签 / 收藏夹管理
- [ ] 键盘快捷键（⌘K 命令面板）
- [ ] 通知（重要源更新推送）
- [ ] sqlite-vec 接入（语义搜索）

## v1.0 — 公开发布

- [ ] 官网落地页
- [ ] 完整文档（用户手册 / API 文档）
- [ ] 隐私政策 / 服务条款
- [ ] 第一个稳定版 release
