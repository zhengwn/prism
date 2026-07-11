---
name: prism-knowledge-base
description: >-
  Query and manage the user's local Prism knowledge base — AI-news items
  fetched from RSS / Hacker News / Bilibili / YouTube / Podcast / arXiv / X and
  distilled into Chinese summaries. Use this whenever the user asks what's new
  in AI, wants to search or recall something they've read or saved in Prism,
  asks to summarize recent AI news, wants to add ("subscribe to" / "follow") a
  new source, or wants to be notified about new items via a webhook. Trigger it
  even when the user doesn't say "Prism" by name — e.g. "什么值得看的 AI 新闻",
  "找一下我之前存的那篇关于 agent 的", "订阅一下 Simon Willison 的博客",
  "有新论文推给我". Reaches the data over the Prism MCP server; the Prism app
  need not be running.
compatibility: Requires the Prism MCP server (`prism-mcp`) configured as an MCP server.
---

# Prism knowledge base

Prism is the user's personal AI-news reader: it fetches from sources they
subscribe to and distills each item into a Chinese title / summary / key
points / tags. This skill exposes that knowledge base through the `prism_*`
MCP tools so you can answer "what's new", recall past reading, manage
subscriptions, and set up push notifications.

The data lives in a local SQLite database that the MCP server opens directly,
so these tools work **whether or not the Prism desktop app is open**.

## When to reach for which tool

Think of it as: **discover → search → drill in**, plus **manage** and
**notify**.

- **`prism_recent_items(limit?, source_id?, status?)`** — the newest items,
  newest first. Your default opener for "what's new in AI", "anything
  interesting today", "catch me up". Returns compact records (id, title,
  summary, tags, source, timestamps).
- **`prism_search(query, source_id?, status?, limit?)`** — ranked full-text
  search across titles, summaries, key points and tags. English matches by
  prefix (`andr` finds "Andreas"); Chinese matches per character. Use it to
  recall something specific: "找我之前看的那篇讲 RAG 的", "search for anything
  about test-time compute".
- **`prism_get_item(item_id)`** — the full record for one item (bilingual
  titles/summaries, key points, tags, source, per-source metadata like audio
  or video URLs). Call it after a list/search when the user wants detail or
  the original link.
- **`prism_list_sources()`** — every source the user follows (id, name, kind,
  enabled, item count). Use it to answer "what am I subscribed to" and to get
  a `source_id` for filtering the tools above.

### Managing subscriptions (writes)

- **`prism_subscribe(name, kind, url, config?)`** — follow a new source.
  Created immediately but fetched on Prism's next sync (not instantly). Config
  is validated up front, so you'll get a clear error if e.g. an X source has
  no bridge. Per-kind shape:
  - `rss` / `blog` / `podcast`: pass the feed `url`, no config.
  - `arxiv`: `config={"categories": ["cs.AI", "cs.LG"]}`, `url=""`.
  - `x`: `url="@handle"` (or profile URL) plus
    `config={"bridge": "https://rsshub.example.com"}`, or a direct
    `config={"feed_url": "..."}`.
  - `youtube`: `config={"channel": "@handle|UC…|url"}` or
    `config={"video": "id|url"}`, `url=""`.
  - `bilibili`: `config={"mid": "…"}` (UP 主) or `config={"bvid": "BV…"}`,
    `url=""`.
- **`prism_set_source_enabled(source_id, enabled)`** — pause/resume a source.
  There is **no delete** — pausing is the reversible, safe way to stop one.

### Push notifications (webhooks)

- **`prism_register_webhook(url, source_id?, tag?)`** — Prism POSTs new items
  to `url` after each sync, optionally filtered to one source and/or one
  Chinese tag. The response includes an HMAC **signing secret shown only
  once** — capture it so the receiver can verify the `X-Prism-Signature`
  header. The URL must be a public https endpoint (localhost / private IPs are
  rejected for safety).
- **`prism_list_webhooks()`** / **`prism_set_webhook_enabled(id, enabled)`** —
  inspect and pause webhooks. No delete; disable instead.

## Habits that make you useful here

- **Start broad, then narrow.** For an open-ended "what's new", call
  `prism_recent_items` first; reach for `prism_search` when the user names a
  topic. Don't guess item ids — get them from a list/search, then
  `prism_get_item`.
- **Summarize in the user's language.** Items are distilled into Chinese; when
  the user writes Chinese, answer in Chinese and lead with the `title` /
  `summary` fields (already Chinese-preferring). Cite the source name and link
  the `url` so they can open the original.
- **Check before you add.** Call `prism_list_sources` before `prism_subscribe`
  so you don't create a duplicate, and tell the user a new source won't have
  items until Prism's next sync.
- **Confirm side effects.** Subscribing, pausing a source, and registering a
  webhook change the user's setup — say what you're about to do and, for
  webhooks, surface the one-time secret clearly.

## Example flows

**"最近 AI 有什么值得看的?"**
→ `prism_recent_items(limit=10)` → summarize the top few in Chinese with
source + link; offer `prism_get_item` for anything they want to read fully.

**"帮我找找之前那篇讲 agent 记忆的"**
→ `prism_search(query="agent 记忆")` → if several hits, list titles; on their
pick, `prism_get_item` for the full summary and link.

**"订阅一下 Simon Willison 的博客"**
→ `prism_list_sources` (dedupe) → `prism_subscribe(name="Simon Willison",
kind="rss", url="https://simonwillison.net/atom/everything/")` → note it'll
appear after the next sync.

**"有关于大模型的新内容就通知我的服务 https://my.app/prism-hook"**
→ `prism_register_webhook(url="https://my.app/prism-hook", tag="大模型")` →
hand back the signing secret once and explain the `X-Prism-Signature` header.
