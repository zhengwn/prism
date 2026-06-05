"""In-memory fixtures for v0.1.

These will be replaced by SQLite + real fetchers in v0.2."""

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


def _make_items() -> list[KnowledgeItem]:
    items: list[KnowledgeItem] = [
        KnowledgeItem(
            id="itm_001",
            source_id="src_001",
            source_name="Andrej Karpathy Blog",
            title="Software 2.0 and the rise of neural networks",
            url="https://karpathy.ai/software-2.0/",
            author="Andrej Karpathy",
            published_at=NOW - timedelta(days=2),
            fetched_at=NOW - timedelta(hours=2),
            status=ItemStatus.starred,
            content_type=ContentType.article,
            summary=(
                "Karpathy's foundational essay on treating neural networks as a new kind of software "
                "where the source code is the dataset and the compiler is the optimizer. Argues that "
                "most production ML systems are already 2.0."
            ),
            key_points=[
                "Datasets are the new source code — small in syntax, large in semantic content.",
                "Gradient descent is the compiler that turns datasets into programs.",
                "2.0 stacks are more homogeneous, easier to compose, and benefit more from compute.",
            ],
            tags=["llm", "ml-systems", "essay"],
        ),
        KnowledgeItem(
            id="itm_002",
            source_id="src_002",
            source_name="Simon Willison's Weblog",
            title="Claude can now control your browser",
            url="https://simonwillison.net/2026/May/22/claude-browser/",
            author="Simon Willison",
            published_at=NOW - timedelta(hours=14),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.unread,
            content_type=ContentType.post,
            summary=(
                "Anthropic shipped a Computer-Use mode for Claude that can drive a real browser via "
                "Playwright. Simon walks through what works, what's still flaky, and the safety "
                "implications of giving an LLM a cursor."
            ),
            key_points=[
                "Computer-use is the new tool-use — anything you can click, an LLM can click.",
                "Latency is the bottleneck; the agent loops are slow on multi-step tasks.",
                "Sandboxing matters: agents will absolutely try to read /etc/passwd if you let them.",
            ],
            tags=["claude", "agents", "browser"],
        ),
        KnowledgeItem(
            id="itm_003",
            source_id="src_002",
            source_name="Simon Willison's Weblog",
            title="Notes on the new Gemini 3 embedding model",
            url="https://simonwillison.net/2026/May/19/gemini-3-embeddings/",
            author="Simon Willison",
            published_at=NOW - timedelta(days=1),
            fetched_at=NOW - timedelta(hours=1),
            status=ItemStatus.unread,
            content_type=ContentType.post,
            summary=(
                "Gemini released a 2048-dim embedding model with a 1M-token context window. "
                "Simon benchmarks it against OpenAI's text-embedding-3-large on a personal RAG corpus."
            ),
            key_points=[
                "2048 dim is plenty for most retrieval tasks — don't over-engineer with 3072.",
                "Long-context embeddings let you skip chunking for most docs.",
                "Pricing dropped 60% YoY — embedding is now a commodity.",
            ],
            tags=["gemini", "embeddings", "rag"],
        ),
        KnowledgeItem(
            id="itm_004",
            source_id="src_003",
            source_name="Two Minute Papers (YouTube)",
            title="Drives in 4K: neural video generation hits photorealism",
            url="https://youtube.com/watch?v=abc123",
            published_at=NOW - timedelta(days=3),
            fetched_at=NOW - timedelta(hours=6),
            status=ItemStatus.unread,
            content_type=ContentType.video,
            duration_sec=185,
            summary=(
                "New diffusion-based video model generates 30-second 4K clips with consistent lighting, "
                "physics, and camera motion. Two Minute Papers walks through the architecture and "
                "highlights the few cases where it still fails."
            ),
            key_points=[
                "Spatiotemporal attention is the key — naive frame-by-frame falls apart by frame 30.",
                "Lighting and reflections are the next frontier.",
                "Open weights expected, which will change the indie filmmaking pipeline fast.",
            ],
            tags=["video", "diffusion", "research"],
        ),
        KnowledgeItem(
            id="itm_005",
            source_id="src_004",
            source_name="Latent Space Podcast",
            title="The state of open-source LLMs in 2026 — with the Llama team",
            url="https://latent.space/episode/open-llm-2026",
            author="Latent Space",
            published_at=NOW - timedelta(days=5),
            fetched_at=NOW - timedelta(days=1),
            status=ItemStatus.read,
            content_type=ContentType.audio,
            duration_sec=3540,
            summary=(
                "Wide-ranging conversation on where open-weights LLMs are winning, where closed "
                "models still dominate, and what the licensing landscape looks like heading into "
                "the next training cycle."
            ),
            key_points=[
                "Open models are within 6 months of frontier on most benchmarks.",
                "Synthetic data from closed models is now the dominant pre-training fuel — and that's a problem.",
                "On-device inference is the new battleground; 7B fits comfortably on M-series Macs.",
            ],
            tags=["llama", "open-source", "llm"],
        ),
    ]
    return items


ITEMS: list[KnowledgeItem] = _make_items()
