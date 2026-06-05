# Prism — Roadmap

> 公开 v1.0 之前的规划。v0.1 是当前 sprint。

## v0.1 — Hello Prism ✅ (in progress)

- [x] Tauri 2 壳工程
- [x] React + TS + Vite 前端骨架
- [x] Python FastAPI sidecar（内存假数据）
- [x] 三栏布局（侧栏 + 内容区 + 详情区）
- [x] 路由（Inbox / Knowledge / Sources / Settings）
- [x] 假数据端到端跑通
- [ ] 图标（替换占位）
- [ ] 暗/亮主题切换
- [ ] 端到端冒烟测试

## v0.2 — Real Pipeline

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
