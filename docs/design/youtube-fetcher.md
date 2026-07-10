# YouTube Fetcher 设计（v0.2c）

> 状态：设计稿，未实现。目标是最大化复用 Bilibili fetcher（`fetchers/bilibili.py`）已验证的模式。

## 定位

`SourceKind.youtube` 在 `models.py` 里已存在，registry 里没有实现，目前落到 `_NoopFetcher`。本方案补上 `fetchers/youtube.py::YouTubeFetcher`，走和 Bilibili 完全一致的管线：抓元信息 + 字幕 → 拼 markdown → 交给带章节切分的视频提炼 prompt。

## 依赖选型：yt-dlp

`pyproject.toml` 加 `yt-dlp>=2025.x`（纯 Python，字幕提取不需要 ffmpeg）。

理由：官方 Data API 要 API key + 配额，且拿不到自动字幕；yt-dlp 免 key、一个库同时覆盖频道列表 / 视频元信息 / 人工+自动字幕。风险：yt-dlp 随 YouTube 改版需要跟版（月度级），以及未登录请求可能触发 bot-check——PoC 先接受，失败时按 fetcher 契约返回 `[]` 并记 log。

注意 yt-dlp 是同步库，所有调用包 `asyncio.to_thread()`，不能阻塞事件循环（sidecar 是单进程 asyncio）。

## 订阅模式（`source.config_json`）

对齐 Bilibili 的 mid / bvid 两模式：

| config_json | 模式 | 行为 |
|---|---|---|
| `{"channel": "@lexfridman"}` 或 channel URL / UCxxx id | 频道 | 拉最近上传列表，capped 20 条 |
| `{"video": "dQw4w9WgXcQ"}` 或视频 URL | 单视频 | 只抓这一条 |
| `{"playlist": "..."}` | 播放列表 | PoC 不做，留 TODO（同 Bilibili keyword 的处理方式） |

解析函数复用 `_config_get()` 的写法（顶层字符串字段，宽容 int）。

## 抓取流程

频道模式两段式，控制请求量：

1. **列表**：`YoutubeDL({"extract_flat": "in_playlist"})` 抓 `https://www.youtube.com/{channel}/videos`，只拿 entries 的 `id`/`title`，capped `max_videos_per_channel=20`（构造参数，默认同 Bilibili 的 `max_videos_per_up`）。
2. **逐视频**：对每个 video id 跑一次完整 `extract_info(download=False)`，拿 `title / description / uploader / upload_date / duration / subtitles / automatic_captions`。视频之间 sleep `_INTER_VIDEO_SLEEP_SEC = 1.0`（YouTube 对未登录请求比 B 站更敏感，比 Bilibili 的 0.5s 更保守）。

`lookback_days`：签名必须是 `async def fetch(self, source: Source, *, lookback_days: int | None = None)`（`fetchers/base.py` 的硬契约，有 `test_fetcher_registry.py` 回归测试盯着）。与 Bilibili 不同，YouTube 的 `upload_date` 可靠，**要真用**：逐视频阶段发现 `upload_date` 早于 cutoff 即停止翻列表（列表按时间倒序）。

## 字幕选取

对应 `_pick_subtitle_track()` 的四级优先，映射到 yt-dlp 的两个 dict：

1. `subtitles`（人工）里的 `zh-Hans / zh-Hant / zh-*` → 记 `[CC]`
2. `subtitles` 里的 `en` / 任意语言 → `[CC]`
3. `automatic_captions` 里的 `zh-*` → `[AI]`
4. `automatic_captions` 里的 `en`（原声自动字幕）→ `[AI]`

格式选 `json3`（结构化、带毫秒时间戳，最接近 B 站的 `{"body": [{from, to, content}]}`），下载用 httpx（复用 `FETCH_TIMEOUT_SEC`）。解析成 `- [MM:SS] [CC] text` 行——**直接复用** `_subtitle_body_to_markdown()` 的输出格式，因为 `distillers/bilibili_prompt.py` 的 `_CC_PREFIX_RE / _AI_PREFIX_RE` 就是按这个格式切的。json3 的 events 先归一化成 `{"from": sec, "content": str}` 再喂进去（把该函数从 bilibili.py 挪到 `fetchers/_subtitle.py` 共享，避免跨 fetcher import）。

都没有字幕 → 优雅降级，只用标题 + description（同 Bilibili）。

## RawItem 组装

```python
RawItem(
    url=f"https://www.youtube.com/watch?v={video_id}",
    title=..., content=markdown, published_at=...,  # upload_date 转 UTC
    author=uploader, content_type=ContentType.video,
    duration_sec=int(duration),
    metadata={
        "source_name": source.name, "video_id": video_id,
        "channel_id": ..., "subtitle_source": ..., "subtitle_kind": ...,
        "feed_kind": "youtube",       # ← 关键，见下节
    },
)
```

markdown 结构复用 `_video_to_markdown()` 的骨架（元信息 header + 简介 + fenced 字幕块），字段名换成"频道主 / 视频 ID"。

## 提炼 prompt：泛化而不是复制

`distillers/base.py` 目前只认 `should_use_bilibili_prompt(raw)`（检查 `metadata.feed_kind == "bilibili"`）。长视频字幕的问题（1-2 万字、章节切分、CC/AI 混排）YouTube 和 B 站完全一样，所以：

- `bilibili_prompt.py` 改造为 `video_prompt.py`，`is_bilibili()` 泛化为 `is_video_transcript()`，接受 `feed_kind in {"bilibili", "youtube"}`；保留 `bilibili_prompt.py` 做 re-export 以免动 31 个既有测试的 import。
- prompt 文案里"B 站/UP 主"字样改为按 `feed_kind` 插值（"YouTube/频道主"）。

**教训回顾**：v0.2c 复查发现过 `is_bilibili()` 检查 `source_kind` 而 fetcher 实际打 `feed_kind` 的字段错位 bug。新增 fetcher 时必须加一条"真实 fetcher 产出的 RawItem 能被 prompt 检测函数命中"的集成测试，不允许只用手写 fixture。

## 前端接入

对照 Bilibili 的接入面（commit `e95dc50`）：

- `SourcesPage`：Add Source dialog 加 youtube kind，输入框收 channel URL / 视频 URL，前端解析出 `config_json`
- `DetailPanel`：`youtube-nocookie.com/embed/{video_id}` iframe 播放器（用 metadata.video_id）
- i18n：`zh.json` / `en.json` 逐 key 对齐加（记住 `_keyIndex` 死数组的教训，不加任何无引用 key）

## 测试

对照 `test_bilibili_fetcher.py`（18 case）的结构：

- yt-dlp import 用 try/except 守卫（同 `_bili_user` 的 monkeypatch 模式），单测全部 mock，不打真网络
- 纯函数单测：json3 → markdown、字幕四级选取、channel 字符串解析（@handle / URL / UCxxx）、upload_date cutoff
- 契约测试：`test_fetcher_registry.py` 加 youtube 注册 case + `lookback_days` kwarg 回归 case
- prompt 集成：真实 `YouTubeFetcher` 构造的 RawItem 过 `is_video_transcript()` 必须为 True

## 实施顺序

1. `fetchers/_subtitle.py` 抽共享（纯重构，跑绿 175 case）
2. `fetchers/youtube.py` + 注册 + 单测
3. prompt 泛化 + 集成测试
4. 前端 + i18n
5. 种子源加 1-2 个 AI 频道，端到端 smoke

## 未决问题

- yt-dlp 被 YouTube bot-check 拦截时是否支持用户提供 cookies 文件？（PoC：不支持，记 log）
- v0.4 PyInstaller 打包时 yt-dlp 的体积与更新策略（yt-dlp 需要频繁更新，打进二进制会过期）
