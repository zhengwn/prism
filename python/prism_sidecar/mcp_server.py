"""Prism MCP server (stdio).

Exposes the local Prism knowledge base (``$PRISM_DATA_DIR/data.db``,
default ``~/.prism/data.db``) to MCP clients — Claude Code / Cursor /
OpenCode and friends. The Prism app does NOT need to be running: this
process opens the SQLite database itself.

Mostly read; the few write tools (prism_subscribe, prism_set_source_enabled,
prism_register_webhook, prism_set_webhook_enabled) are explicit and carry
``readOnlyHint=False``. There is deliberately NO delete/unsubscribe tool —
deleting a source cascade-deletes all its items, which is too destructive
for a one-shot agent call; disable a source or webhook instead. We reuse the
sidecar's ``init_db()`` (idempotent, runs migrations, so the FTS index is
always present) and the ``store.py`` read/write functions verbatim rather
than duplicating query logic. Writes go straight to SQLite — ``POST
/api/sources`` is a pure pass-through over ``store.create_source`` and needs
the app running, so direct writes lose nothing and keep the "app need not
run" property. WAL + ``busy_timeout`` make the cross-process pattern safe
while the app is syncing.

Run: ``uv run prism-mcp`` (or ``prism-mcp`` once installed).

stdio discipline: stdout is the JSON-RPC channel. Never print() here;
all logging goes to stderr (configured in ``main``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from prism_sidecar import __version__, config, store, webhooks
from prism_sidecar.db import close_db, init_db
from prism_sidecar.fetchers.base import FetchError
from prism_sidecar.fts5 import sanitize_fts5_query
from prism_sidecar.models import KnowledgeItem, Source, SourceKind

log = logging.getLogger(__name__)

# Parity with the REST API's cap (app.py: Query(50, ge=1, le=200)).
MAX_LIMIT = 200

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_READ_WRITE = ToolAnnotations(readOnlyHint=False)

# Mirrors models.ItemStatus. A Literal (not the enum) so the values land
# verbatim in the tool's JSON schema and bad input fails validation
# before the tool body runs.
Status = Literal["unread", "read", "archived", "starred"]


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """Open the shared DB singleton for the lifetime of the server.

    ``init_db`` is idempotent and runs the schema migrations, which is
    what guarantees the ``items_fts`` FTS5 index exists even if this
    process is the first thing to ever touch the DB on this machine.
    """
    fresh = not config.PRISM_DB_PATH.exists()
    await init_db()
    if fresh:
        log.warning(
            "Prism DB did not exist at %s — created an empty one. "
            "Launch the Prism app and sync sources to populate it.",
            config.PRISM_DB_PATH,
        )
    try:
        yield None
    finally:
        await close_db()


mcp = FastMCP(
    "prism",
    instructions=(
        "Read-only access to the user's local Prism knowledge base: "
        "AI-news items fetched from RSS / Hacker News / Bilibili / "
        "YouTube / Podcast / arXiv / X sources and distilled into "
        "Chinese summaries. Start with prism_list_sources or "
        "prism_recent_items to see what is available; use prism_search "
        "for full-text lookup (both English and Chinese queries work). "
        "Fetch full detail for one item with prism_get_item."
    ),
    lifespan=_lifespan,
)


def _clamp(limit: int) -> int:
    # Defense-in-depth: FastMCP already rejects out-of-range values via
    # the Field(ge/le) schema, but these functions are also called
    # directly (tests, future reuse), so clamp in-body too.
    return max(1, min(int(limit), MAX_LIMIT))


_BRIEF_KEYS = (
    "id",
    "sourceId",
    "sourceName",
    "url",
    "title",
    "summary",
    "tags",
    "author",
    "publishedAt",
    "status",
    "contentType",
    "durationSec",
)


def _brief(item: KnowledgeItem) -> dict[str, Any]:
    """Compact projection for list results (token economy for the caller).

    A strict subset of the REST ``/api/items`` camelCase shape.
    ``title`` / ``summary`` / ``tags`` are the zh-preferring compat shims
    filled in by ``KnowledgeItem.model_post_init``, so a Chinese-distilled
    item shows its Chinese title here.
    """
    full = item.model_dump(by_alias=True, mode="json")
    return {k: full[k] for k in _BRIEF_KEYS}


@mcp.tool(annotations=_READ_ONLY)
async def prism_search(
    query: Annotated[
        str,
        Field(
            description=(
                "Full-text query over titles, summaries, key points and "
                "tags. English words match by prefix ('andr' finds "
                "'Andreas'); Chinese matches per character, including "
                "mid-word ('协作' finds '开源协作新工具'). The index is "
                "bilingual — both the original English and the distilled "
                "Chinese fields are searched."
            )
        ),
    ],
    source_id: Annotated[
        Optional[str],
        Field(description="Restrict to one source id (from prism_list_sources)."),
    ] = None,
    status: Annotated[
        Optional[Status],
        Field(description="Filter by read status."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 20,
) -> dict[str, Any]:
    """Search the Prism knowledge base with ranked full-text search.

    Returns matching items ordered by relevance. Each result carries id,
    title/summary (Chinese preferred when distilled), tags, source name
    and timestamps. Use prism_get_item with a returned id for the full
    record including key points and per-source metadata.
    """
    # store.list_items(q=...) deliberately falls back to the recent-items
    # path when the sanitizer rejects the query — right for the inbox
    # search box, wrong here: an agent would mistake unrelated recent
    # items for search hits. Pre-check and fail loudly instead.
    if sanitize_fts5_query(query) is None:
        raise ToolError(
            "Query contained no searchable characters (letters, digits or "
            "CJK). Try a real word, e.g. 'agent' or '开源'."
        )
    items = await store.list_items(
        source_id=source_id, status=status, q=query, limit=_clamp(limit)
    )
    return {"count": len(items), "items": [_brief(i) for i in items]}


@mcp.tool(annotations=_READ_ONLY)
async def prism_recent_items(
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 20,
    source_id: Annotated[
        Optional[str],
        Field(description="Restrict to one source id (from prism_list_sources)."),
    ] = None,
    status: Annotated[
        Optional[Status],
        Field(description="Filter by read status."),
    ] = None,
) -> dict[str, Any]:
    """List the newest items in the Prism knowledge base, newest first.

    A good first call to see what the user has been reading lately.
    Ordered by publish time descending. Use prism_get_item for full
    details of any returned id.
    """
    items = await store.list_items(
        source_id=source_id, status=status, limit=_clamp(limit)
    )
    return {"count": len(items), "items": [_brief(i) for i in items]}


@mcp.tool(annotations=_READ_ONLY)
async def prism_get_item(
    item_id: Annotated[
        str,
        Field(description="Item id from prism_search / prism_recent_items."),
    ],
) -> dict[str, Any]:
    """Fetch one knowledge item with every field.

    Includes the bilingual titles and summaries (titleEn / titleZh /
    summaryEn / summaryZh), keyPointsZh, tagsZh, author, timestamps,
    read status and metadataJson (per-source extras such as audio/video
    URLs or subtitle info).
    """
    item = await store.get_item(item_id)
    if item is None:
        raise ToolError(
            f"No item with id {item_id!r}. Ids come from prism_search or "
            "prism_recent_items — do not guess them."
        )
    return item.model_dump(by_alias=True, mode="json")


@mcp.tool(annotations=_READ_ONLY)
async def prism_list_sources() -> dict[str, Any]:
    """List every source the user subscribes to in Prism.

    Returns id, name, kind (rss / bilibili / youtube / podcast / arxiv /
    x / blog), url, enabled flag, itemCount and lastSyncedAt. Use the
    ids to filter prism_search / prism_recent_items.
    """
    sources = await store.list_sources()
    return {
        "count": len(sources),
        "sources": [s.model_dump(by_alias=True, mode="json") for s in sources],
    }


# ---- write tools ----------------------------------------------------------

# Kinds whose config validity can only be known at fetch time. We run the
# fetcher's OWN validator against a transient Source so a bad config is
# rejected at subscribe time (a clear ToolError) instead of silently
# becoming a source that fails at the next sync. `POST /api/sources` does
# NOT do this — it's a value-add of the tool.
_SUBSCRIBE_KINDS = "rss / blog / podcast / arxiv / x / youtube / bilibili"


def _validate_source_config(kind: SourceKind, url: str, config: dict[str, Any]) -> None:
    """Raise ToolError if a source of this kind/url/config can't be fetched.

    Reuses the per-kind validators the fetchers already expose so the error
    message the agent sees is the same one it would eventually hit at sync.
    """
    probe = Source(id="preview", name="preview", kind=kind, url=url, config_json=config)
    try:
        if kind == SourceKind.arxiv:
            from prism_sidecar.fetchers.arxiv import parse_categories

            parse_categories(probe)  # None (defaults) or raises FetchError
        elif kind == SourceKind.x:
            from prism_sidecar.fetchers.x import resolve_feed_url

            resolve_feed_url(probe)
        elif kind == SourceKind.youtube:
            from prism_sidecar.fetchers.youtube import parse_channel_ref, parse_video_ref

            channel = config.get("channel")
            video = config.get("video")
            if config.get("playlist"):
                raise FetchError("playlist-mode YouTube sources are not implemented yet", retryable=False)
            if channel:
                if not parse_channel_ref(str(channel)):
                    raise FetchError(f"unrecognized channel ref: {channel!r}", retryable=False)
            elif video:
                if not parse_video_ref(str(video)):
                    raise FetchError(f"unrecognized video ref: {video!r}", retryable=False)
            else:
                raise FetchError(
                    "youtube needs config {\"channel\": ...} or {\"video\": ...}",
                    retryable=False,
                )
        elif kind == SourceKind.bilibili:
            if not (config.get("mid") or config.get("bvid")):
                raise FetchError(
                    "bilibili needs config {\"mid\": ...} (UP 主) or {\"bvid\": ...} (单视频)",
                    retryable=False,
                )
        else:
            # rss / blog / podcast: an RSS-family feed just needs a URL.
            if not (url or "").strip():
                raise FetchError(f"{kind.value} source needs a feed url", retryable=False)
    except FetchError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_READ_WRITE)
async def prism_subscribe(
    name: Annotated[str, Field(description="Human-readable source name shown in Prism.")],
    kind: Annotated[
        str,
        Field(description=(
            "Source kind, one of: " + _SUBSCRIBE_KINDS + ". "
            "'blog' and 'podcast' are RSS variants."
        )),
    ],
    url: Annotated[
        str,
        Field(description=(
            "Feed URL for rss/blog/podcast. For x, an @handle or profile URL "
            "(needs config.bridge) or a direct feed URL. Ignored for "
            "arxiv/youtube/bilibili (they use `config`); pass \"\" then."
        )),
    ] = "",
    config: Annotated[
        Optional[dict[str, Any]],
        Field(description=(
            "Per-kind config. arxiv: {\"categories\": [\"cs.AI\", ...]}. "
            "x: {\"bridge\": \"https://rsshub.example.com\"} or {\"feed_url\": ...}. "
            "youtube: {\"channel\": \"@handle|UC…|url\"} or {\"video\": \"id|url\"}. "
            "bilibili: {\"mid\": \"…\"} or {\"bvid\": \"BV…\"}. Omit for rss/blog/podcast."
        )),
    ] = None,
) -> dict[str, Any]:
    """Subscribe Prism to a new content source.

    The source is created but NOT fetched immediately — Prism fetches it on
    its next sync (the daily 9am job, or when the user hits "Sync now" in the
    app). Config is validated up front: a bad kind or unusable config is
    rejected here rather than silently failing later. Returns the created
    source (id, kind, url, enabled, itemCount=0). Use prism_list_sources to
    see existing sources first so you don't create a duplicate.
    """
    try:
        source_kind = SourceKind(kind)
    except ValueError:
        raise ToolError(
            f"Unknown kind {kind!r}. Must be one of: {_SUBSCRIBE_KINDS}."
        )
    cfg = config or {}
    _validate_source_config(source_kind, url, cfg)
    source = await store.create_source(
        name=name, kind=source_kind.value, url=url, enabled=True, config_json=cfg
    )
    return source.model_dump(by_alias=True, mode="json")


@mcp.tool(annotations=_READ_WRITE)
async def prism_set_source_enabled(
    source_id: Annotated[str, Field(description="Source id from prism_list_sources.")],
    enabled: Annotated[bool, Field(description="True to resume syncing, False to pause it.")],
) -> dict[str, Any]:
    """Enable or disable a source (reversible pause; not a delete).

    A disabled source is skipped by every sync until re-enabled; its already
    fetched items stay. There is no delete tool — disabling is the safe,
    reversible way to stop a source.
    """
    updated = await store.patch_source(source_id, enabled=enabled)
    if updated is None:
        raise ToolError(
            f"No source with id {source_id!r}. Ids come from prism_list_sources."
        )
    return updated.model_dump(by_alias=True, mode="json")


# ---- webhook tools --------------------------------------------------------


def _webhook_public(webhook: Any, *, reveal_secret: bool = False) -> dict[str, Any]:
    """Serialize a webhook for a tool response. The signing secret is only
    returned in full at registration; listings show the last 4 chars."""
    data = webhook.model_dump(by_alias=True, mode="json")
    if not reveal_secret:
        secret = data.get("secret") or ""
        data["secret"] = f"…{secret[-4:]}" if secret else None
    return data


@mcp.tool(annotations=_READ_WRITE)
async def prism_register_webhook(
    url: Annotated[str, Field(description=(
        "Public https URL to POST new items to. Must resolve to a public "
        "address — loopback / private / link-local hosts are rejected."
    ))],
    source_id: Annotated[Optional[str], Field(description=(
        "Only deliver items from this source id (from prism_list_sources). "
        "Omit to match all sources."
    ))] = None,
    tag: Annotated[Optional[str], Field(description=(
        "Only deliver items carrying this Chinese tag (matches an item's "
        "tagsZh). Omit to match any tag."
    ))] = None,
) -> dict[str, Any]:
    """Register a webhook that receives new Prism items after each sync.

    When a sync produces new items matching the (optional) source_id and tag
    filters, Prism POSTs them to `url` as JSON, signed with HMAC-SHA256 in the
    `X-Prism-Signature: sha256=<hmac>` header. **The signing secret is
    returned only once, now** — store it to verify deliveries. A webhook that
    fails to deliver repeatedly auto-disables. There is no delete tool; use
    prism_set_webhook_enabled to pause one.
    """
    try:
        # Async variant — keeps a slow DNS lookup off the event loop.
        await webhooks.assert_safe_webhook_url_async(url)
    except webhooks.UnsafeWebhookURL as exc:
        raise ToolError(str(exc)) from exc
    if source_id is not None and await store.get_source(source_id) is None:
        raise ToolError(
            f"No source with id {source_id!r}. Ids come from prism_list_sources."
        )
    created = await store.create_webhook(
        url=url, secret=webhooks.generate_secret(), source_id=source_id, tag=tag
    )
    return _webhook_public(created, reveal_secret=True)


@mcp.tool(annotations=_READ_ONLY)
async def prism_list_webhooks() -> dict[str, Any]:
    """List registered webhooks (signing secrets shown as last-4 only).

    Returns id, url, filters (sourceId / tag), enabled, failStreak and
    lastStatus so you can see which webhooks are healthy.
    """
    hooks = await store.list_webhooks()
    return {"count": len(hooks), "webhooks": [_webhook_public(h) for h in hooks]}


@mcp.tool(annotations=_READ_WRITE)
async def prism_set_webhook_enabled(
    webhook_id: Annotated[str, Field(description="Webhook id from prism_list_webhooks.")],
    enabled: Annotated[bool, Field(description="True to resume deliveries, False to pause.")],
) -> dict[str, Any]:
    """Enable or disable a webhook (reversible pause; not a delete)."""
    updated = await store.set_webhook_enabled(webhook_id, enabled)
    if updated is None:
        raise ToolError(
            f"No webhook with id {webhook_id!r}. Ids come from prism_list_webhooks."
        )
    return _webhook_public(updated)


def main() -> None:
    parser = argparse.ArgumentParser(prog="prism-mcp", description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"prism-mcp {__version__}"
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override the Prism data dir (default: $PRISM_DATA_DIR or ~/.prism)",
    )
    args = parser.parse_args()

    # stdio transport: stdout IS the JSON-RPC channel. All logging → stderr.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="[prism-mcp] %(levelname)s %(message)s",
    )

    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser().resolve()
        os.environ["PRISM_DATA_DIR"] = str(data_dir)
        # config.py binds these at import time; db.py reads them at call
        # time *via the config module* (db.py: `_config.PRISM_DB_PATH`),
        # so patching the config module attributes is sufficient — the
        # same trick tests/conftest.py uses.
        config.PRISM_DATA_DIR = data_dir
        config.PRISM_DB_PATH = data_dir / "data.db"

    mcp.run()  # transport defaults to "stdio"


if __name__ == "__main__":
    main()
