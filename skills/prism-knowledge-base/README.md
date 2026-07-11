# prism-knowledge-base skill

An Agent Skill that teaches an assistant to use Prism's MCP tools — querying
the local knowledge base, managing subscriptions, and registering webhooks.

It's a thin layer over the `prism-mcp` server: the MCP server provides the
tools; this skill provides the *judgment* (which tool when, how to summarize,
when to confirm a write).

## Prerequisites

The Prism MCP server must be reachable. From a checkout of this repo:

```bash
claude mcp add prism -- uv --directory /path/to/prism/python run prism-mcp
```

`prism-mcp` opens `~/.prism/data.db` directly, so the Prism desktop app does
**not** need to be running. (Point it elsewhere with
`prism-mcp --data-dir /some/dir` if you keep Prism data outside the default.)

## Install the skill

### Claude Code

Copy the skill directory into your skills folder:

```bash
# project-scoped
cp -r skills/prism-knowledge-base .claude/skills/

# or user-scoped (all projects)
cp -r skills/prism-knowledge-base ~/.claude/skills/
```

Claude Code discovers it on the next session; it triggers automatically when a
request matches the `description` in `SKILL.md` (asking what's new in AI,
searching past reading, subscribing to a source, setting up a webhook…).

### OpenCode

OpenCode has no separate skill packaging — it consumes the same `SKILL.md`.
Copy the two blocks from [`opencode.jsonc`](./opencode.jsonc) into your
`./opencode.json` (or `~/.config/opencode/opencode.json`), fixing the
`--directory` path:

- `mcp.prism` registers the `prism-mcp` stdio server (same one Claude Code
  uses), exposing the `prism_*` tools.
- `instructions: ["./skills/prism-knowledge-base/SKILL.md"]` loads this
  skill's guidance into the agent — the same file, just referenced instead
  of auto-discovered.

### Other runtimes

`SKILL.md` is the portable [Agent Skills](https://agentskills.io) open
standard (YAML frontmatter + Markdown, originally from Anthropic). Any runtime
that follows it — including the Mavis agent — reads this file directly; the
only per-runtime piece is registering the `prism-mcp` MCP server the way that
runtime expects (Claude Code: `claude mcp add`; OpenCode: `opencode.jsonc`).
There is no Prism-specific manifest to maintain per runtime.

## What's inside

- `SKILL.md` — the skill itself: tool-selection guidance, per-kind
  `prism_subscribe` config shapes, webhook flow, and example conversations.
  The `agents:` frontmatter lists the runtimes it's been checked against.
- `opencode.jsonc` — copy-paste OpenCode config (MCP registration +
  `instructions` pointer to `SKILL.md`).
