# .harness/

> Mavis 多 agent 团队配置。只对 Mavis 有意义，**不**影响其它 AI agent（OpenCode、Cursor、Claude Code 等用 `AGENTS.md`）。

## 目录结构

```
.harness/
├── agent.md                 # 顶层 orchestrator = Mavis 自身在这个项目里的 routing brain
├── reins/                   # 项目级 specialist agents
│   ├── frontend-expert/     # src/ — React + TS + Tailwind + shadcn
│   ├── tauri-expert/        # src-tauri/ — Rust + Tauri + sidecar spawn + IPC
│   └── sidecar-expert/      # python/ — FastAPI + LLM + 抓取 + MCP
└── README.md
```

## 工作流

1. 用户给任务 → Mavis 读 `AGENTS.md` + `.harness/agent.md`（自己）→ 判断分派
2. 单文件 / 简单任务 → Mavis 直接做
3. 跨模块 / 复杂任务 → Mavis 加载 `mavis-team` 跑并行 plan，reins 各自执行
4. Verifier 验证 → 引擎回报 CycleReport → Mavis 决定接受 / 重试

## 新增 rein

```bash
mkdir -p .harness/reins/<name>
# 写 agent.md（参考其它 rein 的格式）
# 必填：name / description（给 orchestrator 看的一句话）/ Scope / How you work / Stop when
```

## 注意

- **`agent.md` 不要列 reins** —— daemon 运行时自动注入
- **`description` 必须具体** —— orchestrator 根据这个字段决定分派给谁
- **跨 rein 类型同步**（如 TS ↔ Python）→ orchestrator 协调，**不**让两个 rein 各自改
