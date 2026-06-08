# v0.2a — Provider 切换

> 用户能选 LLM provider（DeepSeek / OpenAI / Anthropic / Ollama / 自定义）。
> 写于 2026-06-08，是 v0.2a 的第二波功能，紧跟 first-sync backfill + redistill。

## 范围

支持 5 个 provider：

| ID | Label | Key | Default model | Env var(s) |
|---|---|---|---|---|
| `deepseek` | DeepSeek | required | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| `openai` | OpenAI | required | `gpt-4o-mini` | `OPENAI_API_KEY` |
| `anthropic` | Anthropic | required | `claude-3-5-sonnet-20241022` | `ANTHROPIC_API_KEY` |
| `ollama` | Ollama (本地) | none | `qwen2.5:7b` | `OLLAMA_API_BASE`（可选） |
| `custom` | Custom (OpenAI-compatible) | required | — | `OPENAI_API_KEY` + `OPENAI_API_BASE` |

**4 预置 + 1 自定义 = 5 个**。Custom 用 OpenAI-compatible 协议 + 让用户填 `base_url` + `model` + `api_key`，能覆盖 MiniMax / 智谱 / Moonshot / 任何兼容接口。

## Cross-track 契约

### Settings API（sidecar HTTP）

```
GET  /api/settings/providers
  → [
      { id, label, requires_key, default_model, fields: ["api_key"] | ["api_key","model"] | ["api_key","base_url","model"] },
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
  注意：custom 模式下 api_key + base_url + model 都要传（首次切换）
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
| `llm-key:deepseek` | `<api_key>` | provider 各自的 key |
| `llm-key:openai` | `<api_key>` | |
| `llm-key:anthropic` | `<api_key>` | |
| `llm-key:custom` | `<api_key>` | Custom 模式下的 key |
| `llm-config:custom` | `{"base_url": "...", "model": "..."}` | Custom 模式下的 model + base_url（JSON 字符串） |

Service name 仍是 `com.prism.desktop`。

### Tauri 启动 sidecar 时的 env 注入

读 keychain 决定 active provider → 注入对应 env var 到子进程：

| Provider | 注入到子进程的 env |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY=<key>` |
| `openai` | `OPENAI_API_KEY=<key>` |
| `anthropic` | `ANTHROPIC_API_KEY=<key>` |
| `ollama` | `OLLAMA_API_BASE=<base_url or default http://127.0.0.1:11434>` |
| `custom` | `OPENAI_API_KEY=<key>` + `OPENAI_API_BASE=<base_url>` |

**关键**：sidecar **永远不**自己读 keychain，所有 key 来自 Tauri 的 env 注入（跟 v0.2a 一样）。keychain 协议不暴露给 sidecar。

## 后端 distillers 架构

```python
# python/prism_sidecar/distillers/
#   base.py            # Distiller Protocol + DistilledItem + DistillerNotConfigured + DistillerKeyInvalid
#   registry.py        # get_distiller(provider, config) -> Distiller
#   deepseek.py        # DeepSeekDistiller
#   openai.py          # OpenAIDistiller
#   anthropic.py       # AnthropicDistiller
#   ollama.py          # OllamaDistiller  (特殊：no_key, 不传 api_key)
#   custom.py          # CustomDistiller  (OpenAI-compatible)
```

每个 distiller 子类的结构基本相同（litellm 抽象 + retry + key invalid detection），但 model string + env 读取不同。

`_looks_like_key_invalid()` 抽到 `base.py` 作为公共 helper（v0.2a 之前在 deepseek.py）。

`settings.py`（新文件）封装：
- `load_active_provider_config() -> { provider, configured, model?, base_url? }`
- `set_active_provider(provider, key, model, base_url) -> None`（写 keychain via Tauri command，**不走 HTTP**）
- `get_provider_schema() -> list[dict]`

sidecar 的 HTTP `/api/settings/llm` POST 端点**不直接写 keychain**——它通过某种 in-process channel 通知 Tauri。**最简单方案**：Tauri 启动 sidecar 时，**sidecar 启动后向 Tauri 发起一个 HTTP request** `GET /__prism_internal/active_provider` 来拉活跃配置。**v0.2a 简化**：sidecar 不管理 active provider 状态，每次需要时**直接调 Tauri command via http**——这个不行，因为 sidecar 是 Tauri 子进程，无法反向 invoke。

**最终方案**：active provider 状态存在 sidecar 启动时的**只读**——Tauri 启动时把 active provider 写入一个临时文件（如 `/tmp/prism-active-provider.json`），sidecar 启动时读这个文件。**更简单**：把 active provider 写到 `~/.prism/active_provider.json`（跟 DB 一起），sidecar 直接读这个文件。

> **决定**：active provider 配置存 `~/.prism/active_provider.json`（plain JSON 文本，**不含 key**）。key 仍在 keychain。sidecar 启动时读 `active_provider.json` 知道当前 provider，再向 Tauri 要 key（HTTP 端点 `/__internal/get_key`）。Tauri command 暴露一个 `get_provider_key(provider)` 给内部使用。

**太复杂了**。**真正最简单的方案**：

> **Tauri 启动 sidecar 时，把当前活跃 provider + 它的 key 一次性注入环境变量**（一个 provider 对应一个 env var）。sidecar 启动时根据 env 变量决定用哪个 provider。
> 用户在 Settings 改 provider → Tauri 写 keychain + 弹个 toast"重启 app 生效"（v0.2a 早期就接受这个）。

**或者更简单**：Tauri 写 keychain → Tauri 主动重启 sidecar 子进程（kill + respawn）→ 重新注入 env。**重启 ~2 秒**。

**最终方案 v2**：
- Tauri 暴露 `set_active_provider(provider, key, model?, base_url?)` command
- 调用时：写 keychain + 重启 sidecar 子进程（带新的 env vars）
- 启动时读 keychain 决定 active provider + 注入 env
- sidecar 启动时从 env `PRISM_ACTIVE_PROVIDER` 知道当前 provider

**简洁、不复杂、能跑**。

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
│ │ Model: [deepseek-chat        ] │           │
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

Custom provider 切到时：
- 显示 `Base URL: [ https://... ]` + `Model: [ ... ]` + `API key: [ ... ]`

Ollama 切到时：
- API key 字段隐藏
- 显示 `Ollama host: [ http://127.0.0.1:11434 ]`（默认）+ `Model: [ qwen2.5:7b ]`

**Ollama 不需要 key**，但 base_url 也要存（如果是远程 ollama）。

**保存按钮** = 调 `setActiveProvider(provider, apiKey?, model?, baseUrl?)`：
- Tauri 写 keychain + 重启 sidecar
- 弹"配置已保存，sidecar 正在重启…"
- health query 重新 fetch
- 待 sidecar 重启完（2 秒）badge 变绿

**i18n 新 key 列表**：
- `settings.provider.label` / `settings.provider.requiresKeyDeepseek` / ...
- `settings.provider.hintDeepseek` / `hintOpenai` / `hintAnthropic` / `hintOllama` / `hintCustom`
- `settings.provider.apiKeyLabel` / `settings.provider.apiKeyPlaceholder.{provider}`
- `settings.provider.advanced` / `settings.provider.modelLabel` / `settings.provider.baseUrlLabel`
- `settings.provider.ollamaHostLabel` / `settings.provider.ollamaHostHint`
- `settings.provider.save` / `settings.provider.saving` / `settings.provider.saveSuccess` / `settings.provider.saveError` / `settings.provider.restarting`

## 测试

### pytest
- `test_provider_registry.py` — 每个 provider 都能构造出对应 Distiller
- `test_openai_distiller.py` — mock litellm，验证 model string + key env
- `test_ollama_distiller.py` — Ollama 不需要 key，passes None
- `test_custom_distiller.py` — Custom 接受 base_url + model
- `test_settings_api.py` — GET/POST /api/settings/llm + /api/settings/providers

### vitest
- `SettingsPage.test.tsx` 加：
  - 切换 provider 时 placeholder 跟着变
  - Ollama 模式不显示 API key 字段
  - Custom 模式显示 base_url + model + api_key 3 个字段

## 风险点

1. **litellm 模型名**：不同 provider 的 model string 必须正确（OpenAI=`gpt-4o-mini`, Anthropic=`claude-3-5-sonnet-20241022`）。litellm 文档为准。
2. **Tauri 重启 sidecar**：`cmd.kill()` + `cmd.spawn()`。要测试 child process 真的能重启 + env vars 正确。
3. **Ollama 没 key 检测**：`_is_key_invalid` 跳过 Ollama 模式（无 key 谈何 invalid）。
4. **Custom 的 base_url 校验**：空字符串、畸形 URL、不带 scheme——前端要 validate。
5. **测试时** mock litellm 时每个 distiller 的 model string 必须不同（避免误判）。
