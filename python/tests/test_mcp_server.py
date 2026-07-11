"""MCP server tests (prism_sidecar/mcp_server.py).

Two layers:

  1. Direct calls to the tool functions against a seeded tmp DB —
     ``@mcp.tool()`` returns the original function unchanged, so the
     decorated names are plain async callables.
  2. One in-memory wire test (``create_connected_server_and_client_session``)
     that runs the real lifespan, so it covers init-on-fresh-machine, tool
     registration/naming, and structured output in a single case.

The autouse ``isolated_data_dir`` fixture (conftest.py) gives every test
its own tmp ``PRISM_DATA_DIR`` and closes the DB singleton around it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from prism_sidecar import mcp_server, store
from prism_sidecar.db import init_db
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.models import ItemStatus


@pytest.fixture
async def initialized():
    await init_db()
    yield


async def _seed():
    """Two sources, three items (one Chinese-distilled, one marked read).

    Mirrors tests/test_fts5.py::_seed so the FTS expectations carry over.
    Returns (source_a, source_b, item_ids).
    """
    src_a = await store.create_source("Blog A", "rss", "https://a.example")
    src_b = await store.create_source("Blog B", "rss", "https://b.example")
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)

    seeds = [
        # (source, url, title, summary, distilled)
        (src_a, "https://a/post-1", "Andreas Kling refuses public PRs",
         "AI era changes how we read maintainer intent", None),
        (src_a, "https://a/post-2", "OpenAI ships GPT-5",
         "Native multimodal with tool use", None),
        (src_b, "https://b/post-3", "Hugging Face 开源协作新工具",
         "中文社区的协作模式在变",
         DistilledItem(
             title_zh="Hugging Face 开源协作新工具",
             summary_zh="中文社区的协作模式在变",
             key_points_zh=["社区驱动", "工具链更新"],
             tags_zh=["开源", "协作"],
         )),
    ]
    item_ids: list[str] = []
    for i, (src, url, title, summary, distilled) in enumerate(seeds):
        raw = RawItem(
            url=url, title=title, content=summary,
            published_at=base + timedelta(hours=i),
        )
        item_id = await store.insert_item_from_raw(src, raw)
        item_ids.append(item_id)
        if distilled is not None:
            await store.update_item_distilled(item_id, distilled)

    # One read item for the status-filter tests.
    await store.update_item_status(item_ids[1], ItemStatus.read)
    return src_a, src_b, item_ids


# ---- prism_search ---------------------------------------------------------


async def test_search_finds_english_word(initialized):
    await _seed()
    out = await mcp_server.prism_search(query="Andreas")
    assert out["count"] == 1
    hit = out["items"][0]
    assert "Andreas" in hit["title"]
    # camelCase parity with the REST API.
    assert "sourceName" in hit and "publishedAt" in hit
    assert "source_name" not in hit


async def test_search_finds_chinese(initialized):
    await _seed()
    out = await mcp_server.prism_search(query="开源")
    assert out["count"] >= 1
    assert any("开源" in i["title"] for i in out["items"])


async def test_search_prefix_match(initialized):
    # The FTS index exists because THIS process ran init_db's migrations —
    # i.e. the MCP server needs no running sidecar.
    await _seed()
    out = await mcp_server.prism_search(query="andr")
    assert out["count"] == 1
    assert "Andreas" in out["items"][0]["title"]


async def test_search_unsearchable_query_raises(initialized):
    # store.list_items(q=...) silently falls back to recent items when the
    # sanitizer returns None (whitespace / pure FTS5-metachar input). That
    # is the exact case the MCP tool must NOT inherit — an agent would
    # mistake unrelated recent items for hits. Use inputs that sanitize to
    # None (see test_fts5.py::test_sanitize_fts5_query_empty_returns_none).
    await _seed()
    for junk in ("   ", "()*:^-"):
        with pytest.raises(ToolError):
            await mcp_server.prism_search(query=junk)


async def test_search_no_match_returns_empty_not_fallback(initialized):
    # A query that DOES sanitize (e.g. "!!!" -> '"!!!"*') but matches no
    # item must return count:0 — proving we take the FTS path, not the
    # recent-items fallback. Honest empty, not misleading hits.
    await _seed()
    out = await mcp_server.prism_search(query="!!!")
    assert out["count"] == 0
    assert out["items"] == []


async def test_search_source_filter(initialized):
    src_a, src_b, _ = await _seed()
    # "协作" only exists in src_b's item; searching src_a must be empty.
    out_b = await mcp_server.prism_search(query="协作", source_id=src_b.id)
    assert out_b["count"] == 1
    out_a = await mcp_server.prism_search(query="协作", source_id=src_a.id)
    assert out_a["count"] == 0


async def test_search_status_filter(initialized):
    await _seed()
    # "GPT" hits the item we marked read.
    out_read = await mcp_server.prism_search(query="GPT", status="read")
    assert out_read["count"] == 1
    assert out_read["items"][0]["status"] == "read"
    out_unread = await mcp_server.prism_search(query="GPT", status="unread")
    assert out_unread["count"] == 0


async def test_limit_clamped_direct_call(initialized):
    await _seed()
    # Direct calls bypass FastMCP's schema validation; the in-body clamp
    # must keep out-of-range values from reaching SQL.
    out = await mcp_server.prism_recent_items(limit=999)
    assert out["count"] == 3
    out = await mcp_server.prism_recent_items(limit=0)
    assert out["count"] == 1


# ---- prism_recent_items ---------------------------------------------------


async def test_recent_items_newest_first(initialized):
    _, _, item_ids = await _seed()
    out = await mcp_server.prism_recent_items()
    assert out["count"] == 3
    # Seeded with published_at ascending → returned newest first.
    assert [i["id"] for i in out["items"]] == list(reversed(item_ids))


# ---- prism_get_item -------------------------------------------------------


async def test_get_item_full_payload(initialized):
    _, _, item_ids = await _seed()
    full = await mcp_server.prism_get_item(item_ids[2])
    assert full["titleZh"] == "Hugging Face 开源协作新工具"
    assert full["keyPointsZh"] == ["社区驱动", "工具链更新"]
    assert full["tagsZh"] == ["开源", "协作"]
    assert full["sourceName"] == "Blog B"
    assert "metadataJson" in full


async def test_get_item_missing_raises_tool_error(initialized):
    await _seed()
    with pytest.raises(ToolError, match="item_nope"):
        await mcp_server.prism_get_item("item_nope")


# ---- prism_list_sources ---------------------------------------------------


async def test_list_sources_item_count(initialized):
    src_a, src_b, _ = await _seed()
    out = await mcp_server.prism_list_sources()
    assert out["count"] == 2
    by_id = {s["id"]: s for s in out["sources"]}
    assert by_id[src_a.id]["itemCount"] == 2
    assert by_id[src_b.id]["itemCount"] == 1
    assert by_id[src_a.id]["name"] == "Blog A"
    # camelCase keys.
    assert "lastSyncedAt" in by_id[src_a.id]


# ---- prism_subscribe (write) ---------------------------------------------


async def test_subscribe_creates_rss_source(initialized):
    out = await mcp_server.prism_subscribe(
        name="My Blog", kind="rss", url="https://example.com/feed.xml"
    )
    assert out["kind"] == "rss"
    assert out["name"] == "My Blog"
    assert out["enabled"] is True
    assert out["itemCount"] == 0
    # It's really in the DB.
    listed = await mcp_server.prism_list_sources()
    assert any(s["id"] == out["id"] for s in listed["sources"])


async def test_subscribe_unknown_kind_raises(initialized):
    with pytest.raises(ToolError, match="Unknown kind"):
        await mcp_server.prism_subscribe(name="x", kind="telepathy", url="u")


async def test_subscribe_x_without_bridge_raises(initialized):
    # resolve_feed_url rejects an @handle with no bridge — surfaced early.
    with pytest.raises(ToolError, match="bridge"):
        await mcp_server.prism_subscribe(name="Simon", kind="x", url="@simonw")


async def test_subscribe_arxiv_bad_categories_raises(initialized):
    with pytest.raises(ToolError, match="categor"):
        await mcp_server.prism_subscribe(
            name="arxiv", kind="arxiv", url="", config={"categories": ["not-a-cat!!"]}
        )


async def test_subscribe_youtube_video_ok(initialized):
    out = await mcp_server.prism_subscribe(
        name="YT", kind="youtube", url="", config={"video": "jNQXAC9IVRw"}
    )
    assert out["kind"] == "youtube"
    assert out["configJson"]["video"] == "jNQXAC9IVRw"


async def test_subscribe_bilibili_without_ref_raises(initialized):
    with pytest.raises(ToolError, match="mid|bvid"):
        await mcp_server.prism_subscribe(name="B", kind="bilibili", url="", config={})


# ---- prism_set_source_enabled (write) ------------------------------------


async def test_set_source_enabled_toggles(initialized):
    created = await mcp_server.prism_subscribe(
        name="Toggle", kind="rss", url="https://e/feed"
    )
    off = await mcp_server.prism_set_source_enabled(created["id"], enabled=False)
    assert off["enabled"] is False
    on = await mcp_server.prism_set_source_enabled(created["id"], enabled=True)
    assert on["enabled"] is True


async def test_set_source_enabled_missing_raises(initialized):
    with pytest.raises(ToolError, match="src_nope"):
        await mcp_server.prism_set_source_enabled("src_nope", enabled=False)


# ---- webhook tools (write) -----------------------------------------------


# Public IP literal — avoids DNS entirely, so the SSRF guard's getaddrinfo is
# deterministic regardless of the test host's resolver. (Real hostnames can
# resolve to private ranges in sandboxed CI, which the guard correctly blocks.)
_PUBLIC_URL = "https://93.184.216.34/hook"


async def test_register_webhook_returns_secret_once(initialized):
    out = await mcp_server.prism_register_webhook(url=_PUBLIC_URL)
    assert out["url"] == _PUBLIC_URL
    assert out["enabled"] is True
    # Full secret revealed at registration.
    assert out["secret"] and not out["secret"].startswith("…")
    # But listing masks it.
    listed = await mcp_server.prism_list_webhooks()
    assert listed["count"] == 1
    assert listed["webhooks"][0]["secret"].startswith("…")


async def test_register_webhook_localhost_rejected(initialized):
    with pytest.raises(ToolError, match="non-public|loopback|resolve"):
        await mcp_server.prism_register_webhook(url="http://localhost:9000/hook")


async def test_register_webhook_metadata_ip_rejected(initialized):
    # 169.254.169.254 = cloud metadata (link-local) — must be blocked.
    with pytest.raises(ToolError, match="non-public|link-local"):
        await mcp_server.prism_register_webhook(url="http://169.254.169.254/latest/meta-data")


async def test_register_webhook_bad_source_filter_raises(initialized):
    with pytest.raises(ToolError, match="src_nope"):
        await mcp_server.prism_register_webhook(url=_PUBLIC_URL, source_id="src_nope")


async def test_set_webhook_enabled_toggle_and_missing(initialized):
    created = await mcp_server.prism_register_webhook(url=_PUBLIC_URL)
    off = await mcp_server.prism_set_webhook_enabled(created["id"], enabled=False)
    assert off["enabled"] is False
    with pytest.raises(ToolError, match="wh_nope"):
        await mcp_server.prism_set_webhook_enabled("wh_nope", enabled=True)


# ---- wire-level integration ----------------------------------------------


async def test_stdio_wire_fresh_db(isolated_data_dir):
    """One in-memory client session against the real FastMCP server.

    Deliberately does NOT use the `initialized` fixture: the lifespan
    itself must create the DB (fresh-machine UX). Covers lifespan init,
    tool registration/naming, and structured output in one pass.
    """
    db_path = isolated_data_dir / "data.db"
    assert not db_path.exists()

    async with create_connected_server_and_client_session(
        mcp_server.mcp._mcp_server
    ) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools.tools)
        assert names == [
            "prism_get_item",
            "prism_list_sources",
            "prism_list_webhooks",
            "prism_recent_items",
            "prism_register_webhook",
            "prism_search",
            "prism_set_source_enabled",
            "prism_set_webhook_enabled",
            "prism_subscribe",
        ]
        assert all(t.description for t in tools.tools)
        # Read tools flag readOnlyHint; write tools must not.
        hints = {t.name: (t.annotations.readOnlyHint if t.annotations else None)
                 for t in tools.tools}
        assert hints["prism_search"] is True
        assert hints["prism_list_webhooks"] is True
        assert hints["prism_subscribe"] is False
        assert hints["prism_set_source_enabled"] is False
        assert hints["prism_register_webhook"] is False
        assert hints["prism_set_webhook_enabled"] is False

        result = await client.call_tool("prism_list_sources", {})
        assert result.isError is False
        assert result.structuredContent == {"count": 0, "sources": []}

    # The lifespan's init_db created the file on the empty data dir.
    assert db_path.exists()
