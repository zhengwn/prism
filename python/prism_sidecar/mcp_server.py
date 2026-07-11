"""Prism read-only MCP server (stdio).

Exposes the local Prism knowledge base (``$PRISM_DATA_DIR/data.db``,
default ``~/.prism/data.db``) to MCP clients — Claude Code / Cursor /
OpenCode and friends. The Prism app does NOT need to be running: this
process opens the SQLite database itself.

Read-only by construction: only query tools are registered, and nothing
in this module writes to the DB. Note the guarantee lives at the *tool*
layer, not the connection layer — we reuse the sidecar's ``init_db()``
(idempotent, runs migrations) so the FTS index is always present and the
query code in ``store.py`` is shared verbatim instead of duplicated.
WAL journal mode makes the cross-process one-writer/N-readers pattern
safe while the app is syncing.

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

from prism_sidecar import __version__, config, store
from prism_sidecar.db import close_db, init_db
from prism_sidecar.fts5 import sanitize_fts5_query
from prism_sidecar.models import KnowledgeItem

log = logging.getLogger(__name__)

# Parity with the REST API's cap (app.py: Query(50, ge=1, le=200)).
MAX_LIMIT = 200

_READ_ONLY = ToolAnnotations(readOnlyHint=True)

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
