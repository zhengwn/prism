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
- `GET  /api/items` — 列出知识条目（支持 `?sourceId&status&q`）
- `GET  /api/items/{id}` — 查看单条知识
- `POST /api/sync` — 触发所有源同步

## v0.1 状态

目前所有数据走内存假数据（`prism_sidecar/data/fixtures.py`），没接真实抓取 / LLM / 持久化。v0.2 会接：

- [ ] SQLite 持久化（aiosqlite）
- [ ] RSS / YouTube / X 抓取器
- [ ] LLM 提炼 pipeline（litellm）
- [ ] 向量存储（sqlite-vec / qdrant）
- [ ] MCP server（stdio）
- [ ] Skill bundle（Mavis / OpenCode / Claude Code）
