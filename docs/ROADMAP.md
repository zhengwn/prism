# Prism — Roadmap

> 公开 v1.0 之前的规划。v0.2c（多源补齐）**已收尾并在本机全量验证过**（2026-07-10）：Bilibili、YouTube、Podcast、arXiv、**X（bridge-RSS PoC）** 五路 fetcher + 错误重试/速率限制 + 优雅关闭 + Vite setApiKey 修复 + **Apply & Restart Sidecar 按钮**；**Playwright 前端 E2E 已跑绿**（Tauri-shell 层留待 WebdriverIO + `@wdio/tauri-service`；macOS 无原版 tauri-driver）。v0.3 已开工：**只读 MCP server（stdio）已落地**（`prism-mcp`，四工具，真实 stdio 冒烟过），见 v0.3 段。
>
> **实测结果**（不再是静态计数）：`uv run pytest` **267/267 绿**（v0.2c 254 + v0.3 MCP server 13）· `npm test` **28/28 绿** · `cargo test` **17/17 绿**（keystore 8 + llm_config 9）· `cargo check --all-targets` 干净 · `npm run build` 干净 · `npm run test:e2e` **5/5 绿**。v0.2c 跑之前修掉了三个真实缺陷；随后又用真实 MiniMax key 跑通了 **distill 端到端**（2 条真实 LLM 调用 + FTS5 索引验证），另修两个附带问题。见下面「收尾验证」小节。

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
- **v0.2c** ✅ 完成（2026-07-10）：多源补齐 + 错误处理，五路 fetcher 全部落地并在本机实测
  - **Bilibili fetcher PoC 已合入**：mid/bvid 两种订阅模式 + CC/AI 字幕合并 + 章节切分，`distillers/bilibili_prompt.py` 专属提炼 prompt；前端 SourcesPage/DetailPanel 已能加 Bilibili 源、嵌入播放器（commits: `fbef1ac` fetcher / `f7db3b7` prompt / `9f2bba8` 接线 / `e95dc50` 前端 / 三个 merge commit）
  - **sidecar 内部拆分**（不在原 roadmap 里，属于顺手的工程债清理）：`app.py` 里的同步 job 编排（并发控制/取消/后台任务）拆到独立的 `pipeline/orchestrator.py`，路由文件从 900+ 行瘦到 500 多行
  - 种子源从 5 个涨到 8 个（新增 3 个 Bilibili AI 资讯 UP 主）
  - 测试：pytest **254/254 绿**（实跑，`--collect-only` 逐文件核对过；此前 ROADMAP/README 写的 175、AGENTS/ARCHITECTURE 写的 222 都是静态计数，且互相矛盾），vitest **28/28 绿**（v0.2b 记的 32 一直是错的）
  - **补做的历史欠账**（这轮全项目复查带出来的，不是新功能）：
    - `i18n/en.json`/`zh.json` 里的 `_keyIndex` 数组（195 条，没有任何代码引用）终于真删了——之前 ROADMAP 一直写着"v0.2b 已清理"，其实一直没删
    - `formatRelativeTime()`（"3h ago" 这种相对时间）之前不管 UI 语言都固定输出英文，违反 AGENTS.md 自己定的"所有用户可见字符串都要过 `t()`"规矩；现在接上 `time.*` i18n key，中文界面显示"3 小时前"
    - `package.json`/`Cargo.toml`/`tauri.conf.json` 的 version 字段（一直是 `0.1.0`，Sidebar 页脚也硬编码 `v0.1.0`）跟 Python sidecar 的 `0.2.0` 对不上——全部统一成 `0.2.0`
    - **严重 bug**：`pipeline/sync.py` 一直用 `fetcher.fetch(source, lookback_days=...)` 调用 fetcher，但 `RSSFetcher`/`HackerNewsFetcher`（以及 registry 里的 `_NoopFetcher`）的 `fetch()` 签名根本不接受这个参数——生产环境里每次同步 RSS/HN 源都会抛 `TypeError`，被 `run_source_sync` 的 `except Exception` 悄悄吞掉、记成 per-source fetch error。8 个种子源里 7 个（除 Bilibili）都受影响。单测没发现是因为 `test_sync.py` 全用接受 `lookback_days` 的 Fake fetcher，从没有测过真实 fetcher 走管线这条路径。现在补上参数、RSSFetcher 真正按 `lookback_days` 算 cutoff，并加了回归测试（`test_rss_fetcher_accepts_lookback_days_kwarg` / `test_hn_fetcher_accepts_lookback_days_kwarg` / 新增 `test_fetcher_registry.py`）
    - `distillers/bilibili_prompt.py` 的 `is_bilibili()` 一直检查 `raw.metadata["source_kind"]`，但两个真实 fetcher（`fetchers/rss.py` / `fetchers/bilibili.py`）打的 tag 其实是 `feed_kind`——"preferred" 的 metadata 检测路径从来没生效过，全靠 URL 里的 `bilibili.com` fallback 兜底，功能上没坏但文档撒谎了。改成检查 `feed_kind`，`test_bilibili_prompt.py` 的 fixture 也同步改名，不再验证一个和生产不一致的假设
    - `src/types/index.ts` 的 `PrismHealth` 缺了 `distillerConfigured` / `dbPath` 两个后端一直在返回的字段——类型声称"跟 models.py 保持同步"，其实一直没有。补齐
    - 又删了 23 个真死的 i18n key（`actions.*` 整个命名空间、`inbox.bilibiliSource`/`syncing`/`distillProgressIdle`、`settings.apiKeyDialog.*`/`setApiKey`/`clearApiKey`、`sources.addDialog.type.*`）——都是早期重构留下的孤儿翻译，代码里没有任何地方引用（含动态 key 拼接）。en.json/zh.json 从 196 key 降到 173，两个文件仍然逐 key 对齐
  - **YouTube fetcher + 错误重试/速率限制**（见下面 v0.2c 清单；设计稿在 `docs/design/`）。新增 pytest：`test_retry.py`（重试矩阵/backoff/Retry-After/throttle）、`test_youtube_fetcher.py`（纯函数 + 三模式 + 契约 + prompt 集成）、`test_sync.py` 追加 FetchError 记账/冷却/真实 RSSFetcher 走管线
  - **Podcast / arXiv fetcher、优雅关闭、Vite setApiKey 修复**（见下面 v0.2c 清单）；Rust 侧 sidecar.rs 的改动已过 `cargo check --all-targets` + `cargo test`
  - **收尾验证**（2026-07-10，全部在本机实跑，非静态计数）：见下面「v0.2c 收尾验证」小节——三个真实缺陷在这一步才暴露出来

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

- [x] i18n `_keyIndex` 数组：v0.2a 为兼容 verifier 写的临时数组，5KB 死重量——**这里之前写着"v0.2b 已清理"，其实没清理，一直留到 v0.2c 才真的删掉**（en.json/zh.json 各 195 条，没有任何代码引用它，见下面 v0.2c 清单）
- [x] Vitest 覆盖再加（v0.2b 从 7 case → 32 case，+25）
- [x] `setApiKey` 在 Vite 调试下抛错（被 React Query onError 接住，prod 无影响）——**v0.2c 已修**（见下面 v0.2c 清单：非 Tauri 路径剥掉 `apiKey` + console.warn）。这个复选框在 v0.2c 收尾后仍未打勾，2026-07-10 补上

## v0.2b — 基础设施重构 + UX 打磨 ✅

实际交付的 v0.2b 不再是"多源补齐"（那一坨挪到 v0.2c），而是一轮**先打地基**的迭代：把 v0.2a 留下的密钥痛点干掉、把交互体验补到能用的水位、把全文搜索这个高频需求先做了。

- [x] **密钥体系重写**：Tauri 端用本地 AES-256-GCM keystore（`~/.prism/keystore.json` + 0600 master key）替换 `tauri-plugin-keyring`，根除 macOS 启动期 keychain 授权弹窗；默认读路径只能查 `{configured: bool}`，明文 key 只在用户主动点"眼睛"按钮时经 `reveal_llm_key` 短暂交给渲染进程；active provider 切换走 `sidecar::restart()` 刷新 env（`b8f8ee8`）
- [x] **LLM provider 瘦身**：5 个 → 2 个（DeepSeek + MiniMax），i18n / 测试 / 注册表同步精简（`428e47d`）
- [x] **提炼实时进度**：SSE 流 + 进度条 + 取消按钮 + 取消 toast，蒸馏中改 key 不卡（`51c91a8` / `5a29e1d`）
- [x] **同步可取消**：sync 异步化 + per-source 边界检查点，长跑可中断（`1769463`）
- [x] **全文搜索**：SQLite FTS5 索引 + 前端 search 框，~5ms 命中；含 CJK prefix + FTS5 语法 sanitizer（`6239674` / `8acdf43`）
- [x] **详情页打磨**：编号 / `#标签` / `**重点**` inline-markdown 渲染（`8acdf43`）
- [x] **Vitest 覆盖扩展**：7 → 32 case（+ 25 个新 case）
- [x] **测试**：114/114 pytest、32/32 vitest、8/8 Rust keystore smoke

## v0.2c — 多源补齐 + 错误处理 ✅

- [x] **Bilibili fetcher PoC**（mid/bvid 两种模式 + CC/AI 字幕合并 + 章节切分专属 prompt + 前端接入）——原计划里没写这条，是实际先做的
- [x] **YouTube fetcher**（yt-dlp + 字幕提取；设计稿 `docs/design/youtube-fetcher.md`）：channel/video 两模式对齐 Bilibili 的 mid/bvid；字幕四级优先（人工 zh → 人工任意 → 自动 zh → 自动 en），json3 → 共享 `fetchers/_subtitle.py` 的 `[CC]/[AI]` 行格式；`lookback_days` 真正生效（upload_date 可靠，列表倒序遇过期即停）；`bilibili_prompt` 泛化为按 `feed_kind` 插值平台措辞（B 站输出逐字不变），`should_use_bilibili_prompt` 扩到 `feed_kind in {bilibili, youtube}`；前端 SourcesPage badge/URL 解析 + DetailPanel youtube-nocookie 播放器 + i18n（en/zh 各 +5 key，逐 key 对齐）
- [x] **错误重试 + 速率限制**（设计稿 `docs/design/retry-and-rate-limit.md`）：
  - 请求级：`fetchers/_retry.py::retry_async`（429/5xx/超时才重试，jitter ±20%，尊重 Retry-After 封顶 30s）+ `HostThrottle` per-host 最小间隔（bilibili 1.0s / youtube 1.5s / 默认 0.2s）；RSS/HN 的手写重试循环收编
  - 源级契约翻转：fetcher 从"绝不 raise"改为"整源失败 raise `FetchError`（带 `retryable` + `partial_items`），单条失败跳过"——`sources.last_error` 从此真正工作（旧契约下 fetch 全挂和"今天没新文章"不可区分，`lookback_days` 签名 bug 正是靠这个机制藏了那么久）
  - 调度级：meta 表存 `fail_streak:{id}` / `retry_after:{id}`，冷却 min(2^n, 24) 小时（non-retryable 直接 24h）；每日 9 点 job 跳过冷却中的源；新增每小时 :30 补跑 job 只挑「失败且过冷却」的源；手动 Sync now 无视冷却；fetch 失败不消耗 first-sync 宽窗口
  - 顺手删掉死配置 `FETCH_INTER_SOURCE_SLEEP_SEC`（定义于 config.py 但全项目无引用）
- [x] **Podcast fetcher**（RSS 变种）：继承 `RSSFetcher`（下载/重试/lookback/FetchError 契约全复用，`_entry_to_raw` 钩子是本轮为此抽的），enclosure → `metadata.audio_url`、`itunes:duration` → `duration_sec`（三种时长形态都认）、episode/season 带上；show notes 做蒸馏正文（转写留给未来 whisper 集成）
- [x] **arXiv fetcher**：新增 `SourceKind.arxiv`（kind 列是 TEXT，无迁移）；export.arxiv.org Atom API，`config_json.categories`（默认 cs.AI/cs.LG/cs.CL）或原生 `query`；submittedDate 倒序 + lookback 截断；throttle 对 arxiv.org 设 3s 间隔（API 条款）；非法 categories 是 `FetchError(retryable=False)` config 错误；前端 kind 下拉 + URL 框兼作分类输入（逗号分隔）
- [x] **顺手**：`blog` kind 从 noop 改为路由到 `RSSFetcher`（blog 源本来就是一条 RSS feed）
- [x] **X fetcher（bridge-RSS PoC）**：选型结论——X 没有免费/稳定/无鉴权的 timeline 接口（FxTwitter 只服务单条推文、无 timeline；无鉴权抓取太脆弱），所以 PoC 走 **bridge-RSS**：X 源指向自托管 RSSHub `/twitter/user/:handle` 或 Nitter RSS，`XFetcher` 继承 `RSSFetcher`（跟 Podcast 一个套路），只做 X 专属三件事——handle/URL 归一化（`@handle`/`x.com/handle`/直接 feed URL）、tweet id+handle+RT/回复元数据（`feed_kind="x"`/`content_type=post`）、短文本专属 distill prompt（`distillers/base.py` 里按 `feed_kind=="x"` 分支，避免长文模板把一条推文注水）；`resolve_feed_url` 对缺 bridge 的配置报 `FetchError(retryable=False)`；registry 接入 `SourceKind.x`，前端 SourcesPage kind 下拉/badge/URL 解析（`feed_url`）+ i18n（en/zh 各 +2）。新增测试 `test_x_fetcher.py`（14 case：handle 解析矩阵/feed-url 解析/status 解析/元数据富化/RT 分类/lookback+错误契约/prompt 路由）+ registry `test_x_source_gets_x_fetcher`。**纯解析逻辑已在沙箱跑过验证；httpx/respx 相关的 fetch 流程待本机 pytest**。FxTwitter 之后可作单推富化层叠加
- [x] **Tauri：`Apply & Restart Sidecar` 按钮**：新增 `sidecar::restart_sidecar` command（后台 fire-and-forget kill+respawn，复用既有 `restart()`），lib.rs 注册；`api.ts::restartSidecar`（Tauri 内 invoke，浏览器 dev no-op + warn）；SettingsPage Sidecar 卡片加按钮 + `useMutation`（成功后等 1.5s 再 invalidate health 反映新进程）+ 错误提示；i18n en/zh 各 +4。**Rust 侧本机需 `cargo check`（沙箱无 cargo）**
- [x] **scheduler / sidecar 优雅关闭**：Python 侧 lifespan shutdown 顺序改为 stop scheduler → `orchestrator.drain_inflight()`（给所有 in-flight job 打 cancel 标记，等它们在 per-source 检查点停下并落盘部分进度，宽限 `PRISM_SHUTDOWN_GRACE_SEC`=4s）→ close_db；Tauri 侧 `kill_existing_child` 在 SIGTERM 后轮询 `try_wait` 最多 5s（刻意大于 Python 的 4s）才硬杀，空闲时 sidecar 秒退不拖慢退出。注意：「跑完当前源再停」就是设计目标——等一次完整 distill run（可能几分钟）不现实
- [x] **Vite 调试下 `setApiKey` 抛错修复**：根因是浏览器 dev 路径把 `apiKey` 原样 POST 给 sidecar，而 sidecar 设计上 400 拒收（key 不过 HTTP）——`api.ts` 注释声称"不发 key"但代码发了。修复：非 Tauri 路径剥掉 `apiKey` + console.warn 说明 key 只能在 Tauri 壳里存；`reveal_llm_key` 的裸 `invoke` 也补了 `isTauri()` 守卫
- [~] **Playwright E2E（前端层已跑绿，Tauri-shell 层留待 WebdriverIO + `@wdio/tauri-service`，macOS 上 tauri-driver 不可用）**：`playwright.config.ts` + `e2e/`（`mock-sidecar.ts` 把 sidecar HTTP 契约全 mock 掉，hermetic、无需 Python/无需 key）+ `test:e2e` script + `@playwright/test` devDep + README。`smoke.spec.ts` **5/5 绿**（本机实跑）：inbox 渲染 / manual sync toast / add-source 建 X 源 / settings 版本+restart 按钮 / **中文 UI 下渲染 `titleZh`**（收尾时新增，见下）。**重要 scope 说明**：Playwright 挂不上 Tauri 的原生 webview（WKWebView/WebView2 无 CDP），所以这层只跑浏览器里的 React UI（`isTauri()=false` 走 HTTP fallback），keystore/`invoke`/`restart_sidecar` 这些壳内路径**没覆盖**。真正的壳级 E2E（真 invoke、AES keystore、sidecar spawn）需要 WebdriverIO + `@wdio/tauri-service`（macOS 无 WKWebView WebDriver，原版 tauri-driver 只支持 Win/Linux，见「仍未覆盖」）——列为后续
- [x] **顺手做的工程债**：`app.py`（900+ 行）里的同步 job 编排逻辑拆到 `pipeline/orchestrator.py`，路由文件瘦身；`AGENTS.md`/`README.md`/`docs/ARCHITECTURE.md` 里过期的测试数字、目录树、provider 数量、种子源数量一并同步
- [x] **i18n `_keyIndex` 真正删除**（195 条死数组，ROADMAP 之前误报"已清理"）+ `formatRelativeTime` 接入 `t()`，不再固定输出英文
- [x] **version 字段统一**：`package.json`/`Cargo.toml`/`tauri.conf.json`/Sidebar 页脚从 `0.1.0` 对齐到 Python sidecar 已经在用的 `0.2.0`

### v0.2c 收尾验证（2026-07-10）

之前所有 v0.2c 的实现都是在**没有 PyPI / npm registry / cargo** 的沙箱里写的，测试数字全靠静态数 `def test_`。这轮在本机第一次真跑，暴露了三个静态计数永远发现不了的缺陷：

1. **`tests/test_retry.py::test_jitter_bounds` 挂了**——不是实现的锅，是测试的锅。`10.0 * (0.8 + 0.4 * 1.0)` 在二进制浮点下等于 `12.000000000000002`，严格的 `<= 12.0` 上界必然失败。改成按 `pytest.approx(8.0 + 4.0 * j)` 比较，同时把两个端点都钉死
2. **`cargo test` 从 v0.2b 起就一直是红的**——`0e10250` 给 `LlmConfigResponse` 加了 `key_last4` / `key_length` 两个字段，但 `tests/llm_config_smoke.rs` 里的结构体字面量没同步，整个 test target 编译不过。ROADMAP 之前写的"8/8 Rust keystore smoke 绿"只覆盖了 `keystore_smoke.rs`，`llm_config_smoke.rs` 压根没编译过。补上字段，并**顺手给这两个字段加了 camelCase 断言**——它们正是 SettingsPage 渲染定长掩码要读的 `keyLast4`/`keyLength`，和 `baseUrl` 一样是会被 `rename_all` 回归打穿的双词字段
3. **`npm test` 挂了**——vitest 的默认 include glob（`**/*.spec.ts`）把 `e2e/smoke.spec.ts` 当成单测收走了，它 `import "@playwright/test"` 直接炸在 import 阶段。`vitest.config.ts` 加 `exclude: [...configDefaults.exclude, "e2e/**"]`

另外 Playwright 的 `inbox` case 一跑就挂：它断言中文标题 `一个新的开源大模型发布`，但 `detectInitialLanguage()` 走的是 `localStorage > navigator.language > en`，而 Chromium 默认 locale 是 `en-US`——UI 其实渲染的是 `titleEn`。**这是测试的假设错了，不是 app 的 bug**。修法：`playwright.config.ts` 显式钉 `locale: "en-US"`（否则结果依赖跑测试的机器 locale），`inbox` case 改断言英文标题，另外**新增一个 `test.use({ locale: "zh-CN" })` 的 case 真正覆盖 `titleZh` 路径**——双语标题选择器是核心功能，之前反而没有测到。

**真实端到端验证**（隔离的 `PRISM_DATA_DIR`，没碰用户的 `~/.prism/data.db`；无 API key 所以 distill 跳过）：

- sidecar 起得来，`/health` 返回 `distillerConfigured` + `dbPath`——**印证了 `PrismHealth` TS 类型补的那两个字段确实存在**
- 真跑 `POST /api/sync/src_simon` 打真实 RSS：**30 条新 item，`error: null`，`last_error: None`**。这条路径正是 `lookback_days` 签名 bug 的案发现场（旧代码必抛 `TypeError`）——单测用的是 Fake fetcher，只有真跑才算数
- 真跑新的 **arXiv fetcher** 打 `export.arxiv.org`：**50 条 item，无错**
- 建一个没配 bridge 的 **X 源**验证错误契约：`last_error` 落到了一条**可操作的配置错误**文案；`_meta` 表里 `fail_streak=1`、`retry_after` 正好是 **+24h**（non-retryable → 24h，符合设计）；而 `first_sync_done` **只对成功的源写入**，失败的 X 源没写——印证了"fetch 失败不消耗 first-sync 宽窗口"
- **优雅关闭**：空闲时 SIGTERM **0.23s** 退出；在 10 源全量 sync 跑到一半时 SIGTERM，**3.87s 内 drain 完退出**（Python 宽限 4.0s < Tauri SIGKILL 5.0s，实测顺序成立），日志有 `job ... cancelled` + `all in-flight sync work drained cleanly`，DB 里那条 job **`status=cancelled` / `items_new=113` / `sources_done=1/10` / `finished_at` 已写**——部分进度确实落盘了，没有留下永远 `running` 的孤儿 job

**真实 distill 端到端验证**（补做于 2026-07-10，用用户已配置的 MiniMax key）：

- 从 `~/.prism/keystore.json` 解出 `minimax` key（AES-256-GCM + AAD `prism-keystore-v1`，GCM 认证通过顺带证明 keystore 未损坏），按 `sidecar.rs` 的 env 契约注入 `MINIMAX_API_KEY` / `MINIMAX_API_BASE` / `PRISM_ACTIVE_PROVIDER`
- `/health` → `distillerConfigured: true`；`POST /api/distill/redistill?batch_limit=2` → **`distilled: 2, failed: 0, key_invalid: false`**，两次真实 LiteLLM → MiniMax-M3 调用，约 25s
- 产出完整落库：`title_zh` / `summary_zh` / 5 条 `key_points_zh` / 5 个 `tags_zh`
- 顺带验了 **distill → FTS5** 链路：搜索「定价」（只出现在 `summary_zh`/`tags_zh`）精确命中刚提炼的那条
- **刻意只跑 2 条**（`batch_limit`），没必要为验证烧掉 193 条 backlog 的 token。仍在隔离的 `PRISM_DATA_DIR` 里跑，用户真实 `~/.prism/data.db` 只读未写

这一轮带出的两个小问题（都不影响功能，已修）：

- 启动 banner 打 `base_url=None`，但 env 覆盖其实生效了——那行直接回显 `active_provider.json` 的 `base_url` 字段，而真正的解析在 `distillers/minimax.py`（`explicit > env > default`）。新增 `settings.resolve_base_url()` 让 banner 报**生效值**，配 4 个回归测试钉死优先级
- `package.json` 的 `dev:keychain-check` 同时引用了 `--test keychain_smoke` 和 `--example dev_keychain_check`，**两者都不存在**（v0.2b keystore 重写时测试改名成 `keystore_smoke.rs`，example 目录压根没建过）——这个 script 从 v0.2b 起跑必然失败。改成 `dev:keystore-check`，指向真实存在的 `keystore_smoke`

**第二轮补验**（2026-07-10 晚，把上一轮"仍未覆盖"清单里能收的都收了）：

- ~~`GET /api/sync/{job_id}` 运行中返回 `sourcesTotal=0`~~ **已修**：`create_job` 新增 `sources_total` 参数（四个调用点建 job 时都已知源数量），运行中轮询从此返回真实 done/total；orchestrator 本来就每源调 `update_job_progress`，缺的只是建行时的 total。加 store 回归测试防 `update_job_progress` 把 total 打回 0。**真实 API 上验证过**：sync 进行中 `"sourcesTotal":1`
- ~~distill 的 `key_invalid` 失败路径~~ **已实测**：用假 key 启动 sidecar → `redistill?batch_limit=3` → `key_invalid: true, distilled: 0`，一次认证失败即提前停批（不烧后续调用），错误文案可操作，item 无半写行（pending 数不变）。顺带验证了 `resolve_base_url` 修复后 banner 报生效值
- ~~Bilibili / YouTube / Podcast 实网抓取~~ **已实测**（隔离 DB）：
  - Bilibili（智东西）：第一次撞上真实 **412 反爬** → `FetchError` 落 `last_error`、`fail_streak=1`；手动重试成功 **20 条**、`fail_streak` 归零、`retry_after` 清空——失败→恢复的完整记账循环被真实网络错误验证
  - Podcast（Lex Fridman feed）：1 条，enclosure → `metadata.audio_url` ✓、`content_type=audio` ✓、`feed_kind=podcast` ✓（该集无 `itunes:duration`/episode 标签，对应字段为空属正常）
  - YouTube（yt-dlp 单视频 `jNQXAC9IVRw`）：1 条，`duration_sec=19` ✓、`feed_kind=youtube` ✓、`subtitle_lang`/`subtitle_kind` 元数据在 ✓

**仍未覆盖**（诚实记账）：

- **Tauri 壳内路径**：`invoke`、AES keystore、sidecar spawn、`restart_sidecar` 按钮的真实点击。**选型已查证（2026-07）**：原版 `tauri-driver` 只支持 Windows/Linux——Apple 不为 WKWebView 提供 WebDriver，本项目开发机是 macOS，此路不通。正路是 **WebdriverIO + `@wdio/tauri-service`**（经 `tauri-plugin-wdio-webdriver` 在 app 内嵌 WebDriver server，原生支持 macOS，无需 CrabNebula 付费 key）。剩实施：加插件（注意用 feature/cfg 门控出 release 包）+ wdio 配置 + 壳级 smoke spec——独立一轮做
- **distill 的 JSON 解析兜底**（全角引号救援等）：`key_invalid` 已实测,但要让真实 LLM 确定性地吐坏 JSON 不可行,这条只能靠单测钉住
- **YouTube channel 模式 / Bilibili 字幕合并的实网路径**：这轮实测的是 YouTube 单视频 + Bilibili 视频列表;channel 列表倒序截断、CC/AI 双轨合并这些分支在真实数据上仍未跑过

## v0.3 — Agent 接口（进行中）

- [x] **MCP server（stdio 模式）**：`prism_sidecar/mcp_server.py` + `prism-mcp` 入口（2026-07-10）。只读切片——复用 sidecar 的 `init_db()` + `store.py` 读函数（零查询重复，FTS 索引由幂等迁移保证），只读性在工具层保证；**app 不用在跑**，`prism-mcp` 进程自己开 SQLite（WAL 跨进程一写多读）。官方 `mcp` SDK pin `>=1.28,<2`（2.0.0b1 有破坏性改名）。stdout 是协议信道，日志全走 stderr。接入：`claude mcp add prism -- uv --directory .../python run prism-mcp`
- [x] **read / search 工具**：`prism_search`（FTS5 排名，垃圾 query 前置拒绝——`store.list_items` 的静默回退对 inbox 正确、对 Agent 是坑）/ `prism_recent_items` / `prism_get_item`（缺失 → `ToolError`）/ `prism_list_sources`；列表工具返回 REST camelCase 形状的精简子集（省 token），get_item 全量
- [ ] **subscribe 工具**（写操作，刻意留到下一切片——先让只读形状被真实 Agent 用一轮）
- [ ] Skill bundle（Mavis / OpenCode / Claude Code 格式）
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
