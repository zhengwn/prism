# v0.2a+ — Provider 切换（DeepSeek + MiniMax）

> 用户能选 LLM provider：DeepSeek（`deepseek-v4-pro`，中文最强，便宜）或
> MiniMax（`MiniMax-M3`，百万上下文，原生多模态，走 OpenAI 兼容协议）。
> 写于 2026-06-09，是对 v0.2a 的瘦身：把 5 个 provider（DeepSeek /
> OpenAI / Anthropic / Ollama / Custom）收成 2 个，把"5 选 1 + 通用
> Custom"换成"硬编码 2 个最常用的"。

## 范围

支持 2 个 provider：

| ID | Label | Key | Default model | Env var(s) |
|---|---|---|---|---|
| `deepseek` | DeepSeek | required | `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| `minimax`  | MiniMax  | required | `MiniMax-M3`      | `MINIMAX_API_KEY` + `MINIMAX_API_BASE` |

> **Model id 命名约定**：`defaultModel` 跟用户填到 Settings 里的
> override 都是**用户友好的产品名**（`deepseek-v4-pro` / `MiniMax-M3`），
> 不带 litellm 路由前缀。`MiniMaxDistiller.__init__` 跟
> `DeepSeekDistiller.__init__` 会在内部自动加 `openai/` / `deepseek/`
> 前缀再传给 litellm——用户不接触这些 implementation detail。

**2 个预设，无自定义**。MiniMax 用 OpenAI-compatible 协议，默认 endpoint
`https://api.minimaxi.com/v1`（用户可在 Settings 高级设置里覆盖 base_url
和 model）。

### 为什么砍到 2 个

- v0.2a 上线后 5 个 provider 的代码 + 测试 + i18n 维护成本跟实际使用
  量不匹配（多数用户只用 DeepSeek）。
- "Custom 模式"是为了给 MiniMax / 智谱 / Moonshot / LM Studio / vLLM 用
  OpenAI-compatible 协议——而 MiniMax M3 现在是国内最常用的国产旗舰，
  直接硬编码成 1 等 provider 反而 UX 更好（少一步配置）。
- Ollama 是本地模型，跟云端 distill 流程是不同的产品场景，放进来
  会让 Settings 区块变得冗长。
- OpenAI / Anthropic 走自家协议，没有 miniMax 那种"国内不可用"的压力，
  对国内用户没价值。

## Cross-track 契约

### Settings API（sidecar HTTP）

```
GET  /api/settings/providers
  → [
      { id, label, requires_key, default_model, fields: ["api_key"] },
      ...
    ]

GET  /api/settings/llm
  → { provider, configured: bool, model?, base_url? }
  注意：不返回 api_key 值

POST /api/settings/llm
  body: { provider: str, api_key?: str, model?: str, base_url?: str }
  → { provider, configured: bool }
  副作用：
    1. Tauri 写 keychain（key 永不进 sidecar）
    2. sidecar 内存里更新 active provider
```

### Tauri command（前端 invoke）

```
get_llm_config()           → { provider, configured, model?, base_url? }  // 不返回 key
set_llm_config(config)     → { ok: true }                                  // 写 keychain
get_provider_schema()      → 跟 GET /api/settings/providers 一样
```

### Keychain slots

| Username | Value | 用途 |
|---|---|---|
| `llm-provider:active` | `"deepseek"` | 当前激活的 provider id |
| `llm-key:deepseek`    | `<api_key>`  | DeepSeek key |
| `llm-key:minimax`     | `<api_key>`  | MiniMax key |
| `llm-config:custom`   | `{"base_url": "...", "model": "..."}` | MiniMax 高级覆盖（JSON 字符串，可选） |

Service name 仍是 `com.prism.desktop`。

### Tauri 启动 sidecar 时的 env 注入

读 keychain 决定 active provider → 注入对应 env var 到子进程：

| Provider | 注入到子进程的 env |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY=<key>` |
| `minimax`  | `MINIMAX_API_KEY=<key>` + `MINIMAX_API_BASE=<base_url or default https://api.minimaxi.com/v1>` |

**关键**：sidecar **永远不**自己读 keychain，所有 key 来自 Tauri 的 env 注入。
keychain 协议不暴露给 sidecar。

## 后端 distillers 架构

```python
# python/prism_sidecar/distillers/
#   base.py            # Distiller Protocol + DistilledItem + DistillerNotConfigured + DistillerKeyInvalid
#   registry.py        # get_distiller(provider, model?, base_url?) -> Distiller
#   deepseek.py        # DeepSeekDistiller  (env_key_var=DEEPSEEK_API_KEY, default=deepseek/deepseek-v4-pro)
#   minimax.py         # MiniMaxDistiller   (env_key_var=MINIMAX_API_KEY, OpenAI-compatible 协议)
```

每个 distiller 子类的结构基本相同（litellm 抽象 + retry + key invalid detection），
但 model string + env 读取不同。

`MiniMaxDistiller` 跟 v0.2a 的 `CustomDistiller` 类似：
- 自动给 model 加 `openai/` 前缀（litellm 路由需要）
- 默认 `api_base = https://api.minimaxi.com/v1`，可被 `api_base` 参数或
  `MINIMAX_API_BASE` 环境变量覆盖
- `llm-config:custom` keychain 槽继续保留，复用为 MiniMax 的高级设置
  blob（base_url + model 覆盖）

`_looks_like_key_invalid()` 抽到 `base.py` 作为公共 helper（v0.2a 之前在 deepseek.py）。

`settings.py` 封装：
- `load_active_provider() -> { provider, model?, base_url? }`（不含 key）
- `set_active_provider(provider, *, model, base_url) -> dict`
- `is_provider_configured(provider) -> bool`（检查 active provider 对应的 env var）
- `PROVIDER_SCHEMAS` 列表（2 项：deepseek + minimax）
- `get_provider_schema() -> list[dict]`

> **legacy v0.1 helper 已废弃**：`config.is_distiller_configured()` 只检查
> `DEEPSEEK_API_KEY`，对 MiniMax 用户会误报"未配置"。v0.2a+ 代码路径都用
> `settings.is_provider_configured(active_provider)`。

## Frontend

`SettingsPage` 的 AI 区块改写：

```
┌─ AI 提炼 ─────────────────────────────────────┐
│ 当前：DeepSeek  [✅ 已配置]                    │
│                                              │
│ Provider:  [ DeepSeek ▼ ]                    │
│  └─ hint: "中文最强，便宜"                    │
│                                              │
│ API key:  [ sk-••••••••••• ] [Clear]        │
│                                              │
│ ┌─ 高级设置 ─────────────────────┐           │
│ │ Model: [deepseek/deepseek-v4-pro] │        │
│ └────────────────────────────────┘           │
│                                              │
│ [ 保存 ]                                     │
│                                              │
│ ─────────── divider ───────────              │
│                                              │
│ 当前有 162 条等待蒸馏。                       │
│ [ 重蒸馏所有 pending ]                       │
└──────────────────────────────────────────────┘
```

切到 MiniMax 时：
- Provider dropdown 显示 "MiniMax"
- hint 切换为 "M3 — 百万上下文，OpenAI 兼容"
- API key placeholder 切换为 `ey…`（MiniMax 的 key 格式）
- 高级设置里的 model 默认值变成 `MiniMax-M3`

**保存按钮** = 调 `setLlmConfig({ provider, apiKey?, model?, baseUrl? })`：
- Tauri 写 keychain + 重启 sidecar
- 弹"配置已保存，sidecar 正在重启…"
- health query 重新 fetch
- 待 sidecar 重启完（2 秒）badge 变绿

**i18n 新 key 列表**（与 v0.2a 比对，删的更多加的更少）：
- `settings.provider.label` / `settings.provider.hints.{deepseek|minimax}`
- `settings.provider.apiKey` / `settings.provider.apiKeyPlaceholder.{Deepseek|Minimax}`
- `settings.provider.advanced` / `settings.provider.model` / `settings.provider.current`
- `settings.provider.save` / `settings.provider.saving` / `settings.provider.saveSuccess` / `settings.provider.saveError` / `settings.provider.clearKey`
- 删：`hintOpenai/Anthropic/Ollama/Custom` /
  `apiKeyPlaceholder{Openai,Anthropic,Custom}` / `ollamaHost` / `ollamaHostHint` /
  `baseUrl` / `baseUrlPlaceholder` / `noKeyNeeded`

## 测试

### pytest
- `test_provider_registry.py` — 2 个 provider 都能构造出对应 Distiller
- `test_deepseek_distiller.py` — model string + key env (已存在)
- `test_minimax_distiller.py` — OpenAI 兼容包装 + api_base 解析 + key env
- `test_settings_api.py` — GET/POST /api/settings/llm + /api/settings/providers

### vitest
- `SettingsPage.test.tsx` 加：
  - 切换 provider 时 placeholder 跟着变
  - 2 个 provider 都有 api-key 字段
  - 高级设置（model）始终显示

### cargo test
- `llm_config_smoke.rs` — 覆盖 `KNOWN_PROVIDERS == ["deepseek", "minimax"]`、
  `default_model_for` 新映射、camelCase IPC 契约

## 风险点

1. **litellm 模型名**：DeepSeek 用 `deepseek/deepseek-v4-pro`、MiniMax 用
   `openai/MiniMax-M3`。litellm 文档为准。
2. **MiniMax 的 base_url 校验**：空字符串、畸形 URL、不带 scheme——前端
   高级设置要 validate。
3. **迁移**：v0.2a 用户的 `active_provider.json` 里如果写着
   `openai`/`anthropic`/`ollama`/`custom`，会被 `load_active_provider` 当
   成 unknown provider 自动回退到 `deepseek`。下次保存 Settings 才会持久化
   新的有效 id。无数据丢失，只是 1 次回退日志。
4. **测试时** mock litellm 时 2 个 provider 的 model string 必须不同
   （避免误判）。
