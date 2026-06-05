# Prism

> **Refract the noise. Illuminate the signal.**
> 把 AI 世界，折射给你。

Prism 是一个 **AI 资讯聚合 + 知识提炼** 桌面应用，把分散在你各个角落的视频、文稿、网页等内容，折射成可复用、可被 Agent 调用的结构化知识。

支持 **Windows** 与 **macOS**。

---

## 核心能力

- 📡 **多源订阅** — RSS、YouTube、X/Twitter、播客、博客、PDF、本地文件，一处聚合
- 🧠 **智能提炼** — 不只是收藏，用 LLM 把每条素材压成可检索、可引用的知识单元
- 🔌 **Agent 原生** — 暴露 [Skill](#) 和 [MCP](#) 接口，让其它 Agent 能直接读、写、订阅 Prism 里的知识
- 💻 **本地优先** — 桌面应用，数据归你，不上传云端

---

## Roadmap（占位）

- [ ] v0.1 — 技术栈定型 + 基础框架跑通
- [ ] v0.2 — RSS / 视频链接解析 + 知识提炼 pipeline
- [ ] v0.3 — Skill + MCP 接口（让 Agent 能用 Prism）
- [ ] v0.4 — 跨平台打包（Windows / macOS）
- [ ] v1.0 — 公开发布

> 详细规划见 [`docs/ROADMAP.md`](./docs/ROADMAP.md)（待补）

---

## 项目结构

```
prism/
├── BRAND.md            # 品牌资产（名字 / slogan / 视觉方向）
├── README.md           # 你正在看的
├── docs/               # 设计文档、roadmap、架构
├── assets/             # logo、图标、截图
│   ├── logos/
│   ├── icons/
│   └── screenshots/
├── src/                # 主代码（待技术栈定型后填充）
├── scripts/            # 仓库维护脚本
└── .vscode/            # 编辑器配置
```

---

## 开发

> ⏳ **技术栈待定**。当前仓库处于"命名 + 立项"阶段，下一步确定框架后补 `package.json` / `Cargo.toml` / `pyproject.toml` 等。

跑起来前需要：

1. 选技术栈（候选：Electron / Tauri / Flutter / 其它）
2. 补 manifest 文件
3. 写 `AGENTS.md` + `.harness/` 给 AI agent 团队用

---

## 品牌 & 文案

- 完整品牌指南：见 [`BRAND.md`](./BRAND.md)
- 主 slogan：`Refract the noise. Illuminate the signal.`
- 中文 slogan：`把 AI 世界，折射给你`

---

## License

TBD（待定）
