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

### Other runtimes

The skill is a plain `SKILL.md` (YAML frontmatter + Markdown), which is the
portable Agent Skill format. OpenCode / Mavis packaging is a thin manifest
wrapper around the same file and is tracked as a follow-up in
`docs/ROADMAP.md`.

## What's inside

- `SKILL.md` — the skill itself: tool-selection guidance, per-kind
  `prism_subscribe` config shapes, webhook flow, and example conversations.
