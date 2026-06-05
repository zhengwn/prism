# Prism — Roadmap

> 公开 v1.0 之前的规划。v0.1 是当前 sprint。

## v0.1 — Hello Prism ✅ (in progress)

- [x] Tauri 2 壳工程
- [x] React + TS + Vite 前端骨架
- [x] Python FastAPI sidecar（内存假数据）
- [x] 三栏布局（侧栏 + 内容区 + 详情区）
- [x] 路由（Inbox / Knowledge / Sources / Settings）
- [x] 假数据端到端跑通
- [x] 端到端冒烟测试 (`npm run smoke` / `pwsh scripts/smoke.ps1`)
- [x] 图标（替换占位）
- [x] 暗/亮主题切换
- [x] i18n 英文/中文切换（顺手补上）

## v0.2a — 数据层 + RSS + DeepSeek 提炼（当前 sprint）

最小可用产品。抓真实内容 + 真实提炼 + 真正能用。

- [ ] SQLite 持久化（aiosqlite + sqlite-vec）
- [ ] 通用 RSS fetcher（feedparser + httpx）
- [ ] Hacker News fetcher（Algolia API，按 AI 标签）
- [ ] 5 个种子源：HN + Simon Willison + OpenAI + Anthropic + Hugging Face
- [ ] DeepSeek 提炼 pipeline（litellm 抽象，标题/摘要/标签全部翻译为中文）
- [ ] 数据层双语存储（title_en / title_zh / summary_en / summary_zh / tags_zh）
- [ ] APScheduler 调度：每天早上 9 点自动跑 + 手动 POST /api/sync 触发
- [ ] SourcesPage Add Source 按钮接通（创建/删除/启用切换）
- [ ] InboxPage 顶部 Sync now 按钮
- [ ] SettingsPage 配置 DeepSeek API key
- [ ] Tauri：API key 走 OS keychain
- [ ] pytest 覆盖 fetcher + distiller

## v0.2b — 补齐其他源

- [ ] YouTube fetcher（yt-dlp + 字幕提取）
- [ ] X / Podcast / 博客的 fetcher
- [ ] arXiv fetcher（cs.AI / cs.LG / cs.CL）
- [ ] 错误重试 + 速率限制
- [ ] Vitest 覆盖关键组件

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

- [ ] 标签 / 收藏夹 / 全文搜索
- [ ] 键盘快捷键（⌘K 命令面板）
- [ ] 通知（重要源更新推送）

## v1.0 — 公开发布

- [ ] 官网落地页
- [ ] 完整文档（用户手册 / API 文档）
- [ ] 隐私政策 / 服务条款
- [ ] 第一个稳定版 release

- [ ] SQLite 持久化（aiosqlite / sqlx）
- [ ] RSS 抓取器（feedparser / httpx）
- [ ] YouTube 抓取器（yt-dlp + transcript API）
- [ ] X / 播客 / 博客抓取器
- [ ] LLM 提炼 pipeline（litellm，统一各家 SDK）
- [ ] 知识单元结构化（标题 / 摘要 / 关键点 / 标签）
- [ ] 同步调度（APScheduler / arq）
- [ ] 错误重试 + 速率限制

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

- [ ] 标签 / 收藏夹 / 全文搜索
- [ ] 暗 / 亮主题切换
- [ ] 国际化（英文 / 中文）
- [ ] 键盘快捷键（⌘K 命令面板）
- [ ] 通知（重要源更新推送）

## v1.0 — 公开发布

- [ ] 官网落地页
- [ ] 完整文档（用户手册 / API 文档）
- [ ] 隐私政策 / 服务条款
- [ ] 第一个稳定版 release
