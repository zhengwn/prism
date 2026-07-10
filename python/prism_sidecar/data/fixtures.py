"""Default seed sources for first-run bootstrap.

When the SQLite DB has zero sources, `store.ensure_default_sources` inserts
these rows. v0.2a shipped five: Hacker News (via Algolia), Simon Willison's
weblog, OpenAI Blog, a DeepMind-blog stand-in for Anthropic (Anthropic has
no public RSS feed — see the inline comment on that entry), and Hugging
Face Blog. v0.2c's Bilibili PoC added three more (智东西 / 机器之心 /
PaperWeekly), so `SEED_SOURCES` currently has **8** entries, not 5 — keep
this count in sync with README.md's "首次启动会自动从 fixtures 导入 N 个
种子源" line and AGENTS.md/ARCHITECTURE.md's directory-tree comments if it
changes again.

The "items" list is no longer used — items now come from real fetches. We
keep a small sample of demo items in the legacy `ITEMS` export so older
smoke tests and visual demos still have something to render if a user
runs the sidecar without internet access.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prism_sidecar.models import (
    ContentType,
    ItemStatus,
    KnowledgeItem,
    Source,
    SourceKind,
)

NOW = datetime.now(timezone.utc)


# ----- v0.2a seed sources --------------------------------------------------

SEED_SOURCES: list[dict] = [
    {
        "id": "src_hn",
        "name": "Hacker News (AI stories)",
        "kind": SourceKind.rss.value,
        "url": "https://hn.algolia.com/api/v1/search?tags=story&query=AI&hitsPerPage=20",
        "enabled": True,
        "config_json": {
            "is_hn_algolia": True,
            "keywords": [
                "AI",
                "LLM",
                "GPT",
                "Claude",
                "agent",
                "machine learning",
            ],
        },
    },
    # v0.2b PoC: 3 个 AI 资讯类 B 站 UP 主。
    #
    # mid 验证步骤 (2026-06-16): 用 `bilibili_api.search_by_type(
    # keyword=<name>, search_type=SearchObjectType.USER)` 查到真实 mid,
    # 再 cross-check fans 数确保是官方账号而非重名小号。
    # 3 个 placeholder mid 在 task brief 里都错了 (mid=339137722 实际是
    # 一个叫 kujdhxmqaw 的用户,跟 智东西 无关),现在的数字已经修。
    #
    # mid-to-name 速查:
    #   * 智东西        (mid=31703119,  ~24k fans)  — 资讯整合向
    #   * 机器之心官方  (mid=73414544,  ~90k fans)  — 资讯 + 论文
    #   * PaperWeekly   (mid=368145665, ~33k fans)  — 论文解读
    #
    # 如果 B 站再换 mid 规则,这里手动改 3 个数字即可;
    # 改不动的话 BilibiliFetcher 会优雅降级 (log 失败 + 返回空列表)。
    {
        "id": "src_bili_zhidongxi",
        "name": "B站 · 智东西 (AI 资讯)",
        "kind": SourceKind.bilibili.value,
        "url": "https://space.bilibili.com/31703119",
        "enabled": True,
        "config_json": {
            "mid": "31703119",
        },
    },
    {
        "id": "src_bili_jiqizhixin",
        "name": "B站 · 机器之心 (AI 资讯 + 论文)",
        "kind": SourceKind.bilibili.value,
        "url": "https://space.bilibili.com/73414544",
        "enabled": True,
        "config_json": {
            "mid": "73414544",
        },
    },
    {
        "id": "src_bili_paperweekly",
        "name": "B站 · PaperWeekly (论文解读)",
        "kind": SourceKind.bilibili.value,
        "url": "https://space.bilibili.com/368145665",
        "enabled": True,
        "config_json": {
            "mid": "368145665",
        },
    },
    {
        "id": "src_simon",
        "name": "Simon Willison's Weblog",
        "kind": SourceKind.rss.value,
        "url": "https://simonwillison.net/atom/everything/",
        "enabled": True,
        "config_json": {},
    },
    {
        "id": "src_openai",
        "name": "OpenAI Blog",
        "kind": SourceKind.rss.value,
        "url": "https://openai.com/blog/rss.xml",
        "enabled": True,
        "config_json": {},
    },
    {
        "id": "src_anthropic",
        "name": "Anthropic Engineering (DeepMind mirror)",
        "kind": SourceKind.rss.value,
        # Anthropic doesn't publish a public RSS feed (only the alignment
        # subdomain returns 200 but its /rss.xml is an HTML page). Swap
        # to DeepMind Blog as a comparable frontier-AI source.
        "url": "https://deepmind.google/blog/rss.xml",
        "enabled": True,
        "config_json": {},
    },
    {
        "id": "src_huggingface",
        "name": "Hugging Face Blog",
        "kind": SourceKind.rss.value,
        "url": "https://huggingface.co/blog/feed.xml",
        "enabled": True,
        "config_json": {},
    },
]


# ----- Legacy in-memory source list (kept for v0.1 visual demo) -----------

SOURCES: list[Source] = [
    Source(
        id="src_001",
        name="Andrej Karpathy Blog",
        kind=SourceKind.blog,
        url="https://karpathy.ai/",
        enabled=True,
        last_synced_at=NOW - timedelta(hours=2),
        item_count=3,
    ),
    Source(
        id="src_002",
        name="Simon Willison's Weblog",
        kind=SourceKind.rss,
        url="https://simonwillison.net/atom/everything/",
        enabled=True,
        last_synced_at=NOW - timedelta(hours=1),
        item_count=4,
    ),
    Source(
        id="src_003",
        name="Two Minute Papers (YouTube)",
        kind=SourceKind.youtube,
        url="https://youtube.com/@TwoMinutePapers",
        enabled=True,
        last_synced_at=NOW - timedelta(hours=6),
        item_count=2,
    ),
    Source(
        id="src_004",
        name="Latent Space Podcast",
        kind=SourceKind.podcast,
        url="https://latent.space/feed.xml",
        enabled=True,
        last_synced_at=NOW - timedelta(days=1),
        item_count=1,
    ),
]


# ----- Legacy demo items (rendered only if DB is empty AND no fetch ran) -

def _make_items() -> list[KnowledgeItem]:
    return [
        KnowledgeItem(
            id="itm_001",
            source_id="src_simon",
            source_name="Simon Willison's Weblog",
            title_en="Claude can now control your browser",
            title_zh="Claude 现在可以控制你的浏览器",
            summary_en=(
                "Anthropic shipped a Computer-Use mode for Claude that can drive a real browser via "
                "Playwright. Simon walks through what works, what's still flaky, and the safety "
                "implications of giving an LLM a cursor."
            ),
            summary_zh=(
                "Anthropic 推出了 Claude 的 Computer-Use 模式，可以通过 Playwright 操控真实浏览器。"
                "Simon 详细评测了哪些场景可用、哪些仍不稳定，以及给 LLM 一只光标的安全影响。"
            ),
            key_points_zh=[
                "Computer-use 是新一代 tool-use：能点击的，LLM 就能点。",
                "多步任务时延是瓶颈，agent 循环偏慢。",
                "沙箱至关重要：你不拦它，它一定会去读 /etc/passwd。",
            ],
            tags_zh=["claude", "agent", "浏览器", "工具调用"],
            url="https://simonwillison.net/2026/May/22/claude-browser/",
            author="Simon Willison",
            published_at=NOW - timedelta(hours=14),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.unread,
            content_type=ContentType.post,
        ),
        KnowledgeItem(
            id="itm_002",
            source_id="src_openai",
            source_name="OpenAI Blog",
            title_en="GPT-5: native multimodal reasoning across text, vision and audio",
            title_zh="GPT-5：跨文本、视觉与音频的原生多模态推理",
            summary_en=(
                "OpenAI announces GPT-5 with native multimodal reasoning. The new model unifies "
                "text, image, and audio understanding in a single transformer, with dramatically "
                "improved long-context performance."
            ),
            summary_zh=(
                "OpenAI 发布 GPT-5，原生支持多模态推理。文本、图像、音频理解统一在同一个 "
                "transformer 中，长上下文性能大幅提升。"
            ),
            key_points_zh=[
                "统一架构：不再区分文本 / 视觉 / 音频子模型。",
                "1M token 上下文 + 工具调用原生支持。",
                "推理成本比 GPT-4o 下降 70%。",
            ],
            tags_zh=["gpt-5", "openai", "多模态", "大模型"],
            url="https://openai.com/blog/gpt-5/",
            author="OpenAI",
            published_at=NOW - timedelta(days=2),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.unread,
            content_type=ContentType.article,
        ),
        KnowledgeItem(
            id="itm_003",
            source_id="src_anthropic",
            source_name="Anthropic News",
            title_en="Claude 4.5: best coding model yet, with extended thinking",
            title_zh="Claude 4.5：迄今最强编程模型，引入扩展思考",
            summary_en=(
                "Anthropic releases Claude 4.5 with significant coding improvements and a new "
                "extended-thinking mode that lets the model spend more compute on hard problems."
            ),
            summary_zh=(
                "Anthropic 发布 Claude 4.5，编程能力显著提升，并新增扩展思考（extended "
                "thinking）模式，让模型在难题上花更多算力。"
            ),
            key_points_zh=[
                "SWE-bench 得分首次突破 80%。",
                "扩展思考模式：模型在不确定时主动多算几步。",
                "API 价格不变，新模式按思考 token 计费。",
            ],
            tags_zh=["claude", "anthropic", "编程", "推理"],
            url="https://www.anthropic.com/news/claude-4-5",
            author="Anthropic",
            published_at=NOW - timedelta(days=3),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.unread,
            content_type=ContentType.article,
        ),
        KnowledgeItem(
            id="itm_004",
            source_id="src_huggingface",
            source_name="Hugging Face Blog",
            title_en="SmolLM 3: a 3B model that punches above its weight",
            title_zh="SmolLM 3：以小博大的 30 亿参数模型",
            summary_en=(
                "Hugging Face releases SmolLM 3, a 3B parameter model trained on a carefully "
                "curated mix of web + code + synthetic data. Outperforms larger 7B models on "
                "most reasoning benchmarks."
            ),
            summary_zh=(
                "Hugging Face 发布 SmolLM 3，30 亿参数，在精选的网页 + 代码 + 合成数据上训练。"
                "在大多数推理 benchmark 上超过更大的 7B 模型。"
            ),
            key_points_zh=[
                "30 亿参数在 M2 MacBook 上跑得飞快。",
                "合成数据占比 40%，是新一代小型模型的关键。",
                "完全开源权重 + 训练数据 recipe。",
            ],
            tags_zh=["smollm", "开源", "小模型", "huggingface"],
            url="https://huggingface.co/blog/smollm-3",
            author="Hugging Face",
            published_at=NOW - timedelta(days=4),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.unread,
            content_type=ContentType.article,
        ),
        KnowledgeItem(
            id="itm_005",
            source_id="src_hn",
            source_name="Hacker News (AI stories)",
            title_en="Show HN: I built an open-source ML platform on top of Kubernetes",
            title_zh="Show HN：我用 Kubernetes 搭了一个开源机器学习平台",
            summary_en=(
                "A developer shares their journey building an open-source ML platform. "
                "Built on Kubernetes, supports distributed training, and integrates with "
                "the Hugging Face ecosystem."
            ),
            summary_zh=(
                "一位开发者分享搭建开源机器学习平台的经验。基于 Kubernetes，支持分布式训练，"
                "并与 Hugging Face 生态打通。"
            ),
            key_points_zh=[
                "Kubernetes operator 模式管理 GPU 资源。",
                "内置 spot instance 支持，成本下降 60%。",
                "GitHub star 已破 10k。",
            ],
            tags_zh=["kubernetes", "开源", "mlops", "gpu"],
            url="https://news.ycombinator.com/item?id=42424242",
            author="hn_user_42",
            published_at=NOW - timedelta(days=1),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.starred,
            content_type=ContentType.post,
        ),
    ]


ITEMS: list[KnowledgeItem] = _make_items()
