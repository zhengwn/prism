# Prism Sidecar

Python 后端：负责抓取内容、调用 LLM 提炼、暴露 MCP server。

## 跑起来

```bash
# 装依赖
uv sync

# 开发模式启动
uv run prism-sidecar

# 或者用 npm 脚本（在仓库根目录）
npm run sidecar:dev
```

默认监听 `http://127.0.0.1:8765`。

## 路由

- `GET  /health` — 健康检查
- `GET  /api/sources` — 列出所有订阅源
- `POST /api/sources` — 添加订阅源
- `GET  /api/sources/{id}` — 查看单个源
- `DELETE /api/sources/{id}` — 删除源
- `GET  /api/items` — 列出知识条目（支持 `?source_id&status&q`，`q` 走 FTS5 全文搜索）
- `GET  /api/items/{id}` — 查看单条知识
- `POST /api/sync` — 触发所有源同步（异步 job，轮询 `GET /api/sync/{job_id}`）

完整 API 契约见 `docs/ARCHITECTURE.md`。

## MCP server（v0.3，只读）

把本地知识库暴露给 Claude Code / Cursor / OpenCode 等 Agent。stdio 模式，
**不需要 Prism app 在跑**——`prism-mcp` 进程自己打开 SQLite（WAL 跨进程
一写多读，app 同步中也能查）。

```bash
# 直接跑（stdio，给 MCP 客户端 spawn 用）
uv run prism-mcp

# 接入 Claude Code
claude mcp add prism -- uv --directory /path/to/prism/python run prism-mcp

# 指定数据目录（默认 $PRISM_DATA_DIR 或 ~/.prism）
uv run prism-mcp --data-dir /tmp/prism-test
```

四个只读工具（`subscribe` 等写操作留给后续切片）：

| 工具 | 作用 |
|---|---|
| `prism_search(query, source_id?, status?, limit?)` | FTS5 排名全文搜索（英文前缀匹配、中文逐字，双语索引） |
| `prism_recent_items(limit?, source_id?, status?)` | 最新条目，按发布时间倒序 |
| `prism_get_item(item_id)` | 单条全字段（双语标题/摘要、关键点、metadataJson） |
| `prism_list_sources()` | 全部订阅源（含 itemCount / lastSyncedAt） |

DB 不存在时会建一个空库并在 stderr 提示先启动 Prism 同步；工具返回干净的
`{"count": 0, ...}` 而不是报错。

## 状态（v0.2c 已完成，v0.3 进行中）

- [x] SQLite 持久化（aiosqlite，WAL，FTS5 全文索引）
- [x] 七路抓取器：RSS / HN / Bilibili / YouTube / Podcast / arXiv / X（bridge-RSS）
- [x] LLM 提炼 pipeline（litellm，DeepSeek / MiniMax，全中文输出）
- [x] MCP server（stdio，只读四工具）
- [ ] MCP subscribe 工具 + Webhook（写操作，下一切片）
- [ ] Skill bundle（Mavis / OpenCode / Claude Code）
- [ ] 向量存储（sqlite-vec，留 v0.5）

测试：`uv run pytest -v`。
