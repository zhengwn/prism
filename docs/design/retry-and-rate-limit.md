# 错误重试 + 速率限制 设计（v0.2c）

> 状态：设计稿，未实现。对应 ROADMAP v0.2c 的"错误重试 + 速率限制（per-source 配额 + APScheduler backoff）"。

## 现状与问题

现有机制盘点：

- **请求级重试**：只有 `RSSFetcher._download()` 有（`FETCH_MAX_RETRIES=2` + 指数 backoff `FETCH_RETRY_BACKOFF_SEC=1.0`，config.py 已有这三个环境变量）。`BilibiliFetcher` 的 get_info / get_subtitle / 字幕下载全是一次失败即放弃。
- **错误可见性**：`fetchers/base.py` 的契约是"fetcher 不许 raise，失败返回 `[]` 并记 log"。后果是 `run_source_sync` 里 `stats.error` / `mark_source_error()` 那条路径**几乎永远走不到**——fetcher 把错误吞了，管线只看到 `fetched=0`，和"今天确实没新文章"无法区分。`sources.last_error` 形同虚设。（`lookback_days` 签名 bug 能潜伏那么久，正是这个机制在掩护：TypeError 被记成 per-source fetch error 后无人能看见。）
- **调度级重试**：没有。APScheduler 每天 9 点跑一次，某源今天挂了就等明天，中间不补。
- **速率限制**：各 fetcher 自己 sleep（Bilibili 0.5s/视频），没有统一机制。`FETCH_INTER_SOURCE_SLEEP_SEC` 在 config.py 定义了但**全项目无人引用**——死配置（本次设计核查发现，性质同 `_keyIndex`）。

## 设计总览

三层各管一段，自下而上：

```
请求级   fetchers/_retry.py      同一个 HTTP 请求的瞬时错误，秒级重试
源级     pipeline/sync.py        整源 fetch 失败 → typed error 上抛，管线记账
调度级   scheduler.py + meta     连续失败的源 → 冷却窗口 + 小时级补跑
```

## 1. 请求级：共享重试助手 `fetchers/_retry.py`

把 `RSSFetcher._download` 里的循环抽成通用函数：

```python
async def retry_async(
    fn: Callable[[], Awaitable[T]], *,
    max_retries: int = FETCH_MAX_RETRIES,
    backoff_base: float = FETCH_RETRY_BACKOFF_SEC,
    retryable: Callable[[Exception], bool] = default_retryable,
) -> T: ...
```

- `default_retryable`：httpx 超时/连接错误、HTTP 429/5xx → 重试；4xx（除 429）→ 不重试直接抛
- 429 且带 `Retry-After` header → 用 header 值替代指数 backoff（封顶 30s）
- backoff 公式沿用现有 `backoff_base * 2**(attempt-1)`，加 ±20% jitter（现在没有，多源同时打同一 host 会齐步重试）

接入点：`RSSFetcher._download` 改为薄封装（行为不变，测试不动）；`BilibiliFetcher` 的字幕 JSON 下载、未来 `YouTubeFetcher` 的 json3 下载套用。bilibili-api-python 内部调用不强套（库自身有封装），只包最外层。

## 2. 源级：让错误重新可见（本设计的核心）

**修改 fetcher 契约**（`fetchers/base.py` docstring + 各实现）：

- 新增 `fetchers/base.py::FetchError(Exception)`，字段：`message`、`retryable: bool`、`partial_items: list[RawItem]`（挂掉前已抓到的条目，不浪费）
- 契约从"绝不 raise"改为："**整源不可用**（DNS 失败、列表页 4xx/5xx 重试耗尽、依赖库缺失）→ raise `FetchError`；**单条目失败**（某视频字幕拉不下来）→ 跳过该条、记 log、继续"。即：部分失败自己消化，全量失败必须上抛。
- `run_source_sync` 已有的 `except Exception` 分支天然接住 `FetchError`：`stats.error` + `mark_source_error()` 从此真正工作。额外在该分支里先插入 `partial_items` 再记错。

改动面：RSSFetcher / HackerNewsFetcher / BilibiliFetcher / `_NoopFetcher` 四处 + base.py docstring。`_NoopFetcher` 保持返回 `[]`（kind 未实现不算错误）。

**测试要求**（吸取 `test_sync.py` 全用 Fake fetcher 漏掉签名 bug 的教训）：每个真实 fetcher 加"网络层 mock 成全挂 → 断言 raise FetchError"的 case；`test_sync.py` 加一条真实 `RSSFetcher` + mock transport 走通 `run_source_sync` 记账路径的集成 case。

## 3. 调度级：失败冷却 + 补跑

**状态存储**：不动 db schema，沿用 `first_sync_done:{source_id}` 的 meta-key 模式：

- `fail_streak:{source_id}` — 连续失败次数，成功清零
- `retry_after:{source_id}` — 冷却截止时间 ISO 串

**规则**（在 `run_source_sync` 成功/失败路径末尾维护）：

- 失败第 n 次 → 冷却 `min(2**n, 24)` 小时（1次=2h，2次=4h，3次=8h，≥5次=24h）
- 冷却中的源：手动 "Sync now" **无视冷却**照跑（用户意志优先），跑成功即清零
- `fail_streak >= 10` 的源在 SourcesPage 显示"建议检查/停用"badge（前端读 `last_error` + 新加的 API 字段，不自动禁用——自动禁用对 UP 主停更这类误判太狠）

**补跑 job**：`scheduler.py` 在每日 9 点 job 之外加一个每小时的轻量 job：只挑「上次失败 && 已过冷却 && enabled」的源，复用 `run_all_sync_background` 的锁检查逻辑（`inflight_jobs` / `redistill_running` 互斥不变，撞上就跳过等下个整点）。

## 4. 速率限制：per-host 令牌间隔

统一到 `fetchers/_retry.py::HostThrottle`：进程级单例，`dict[host, last_request_ts]`，`await throttle.wait(url)` 保证同 host 两次请求间隔 ≥ 配置值：

| host 模式 | 最小间隔 | 来源 |
|---|---|---|
| `*.bilibili.com` / `*.hdslb.com` | 1.0s | 现有 0.5s sleep 收编并加严（B 站未登录 ~1 req/s） |
| `*.youtube.com` / `*.googlevideo.com` | 1.5s | YouTube fetcher 设计稿 |
| 默认 | 0.2s | RSS 源通常单请求，形同无限制 |

替代现在散落的 `asyncio.sleep`（Bilibili 的 `_INTER_VIDEO_SLEEP_SEC` 收编进 throttle；死配置 `FETCH_INTER_SOURCE_SLEEP_SEC` 连同 `__all__` 导出一并删除）。这样将来 orchestrator 若做并发（见下），限流不会被绕过。

## 非目标（明确不做）

- **源级并发**：orchestrator.py 的单锁串行模型不动。docstring 里已注明这是 scheduling/product 决策；等 v0.2c 多源全部落地、"一个慢源卡住全部"真的可感知了再改（届时 HostThrottle 已就位，是前置条件之一）。
- **distill 重试**：LLM 调用失败已有 `failed_distill` 计数 + redistill 兜底，且 `DistillerKeyInvalid` 有专门的停止逻辑，本设计不碰。
- **db schema 迁移**：全部走 meta 表。

## 实施顺序

1. `_retry.py`（retry_async + HostThrottle）+ 纯单测（fake clock，不真 sleep）
2. FetchError + 三个真实 fetcher 改契约 + 契约测试
3. `run_source_sync` 记账 + fail_streak/retry_after 维护
4. scheduler 每小时补跑 job
5. 前端 badge + i18n（可切到 YouTube fetcher 之后做）

1-2 优先级最高：它们直接决定 YouTube / X / Podcast / arXiv 四个新 fetcher 按哪套契约写，晚做就要返工四遍。
