// Tiny inline-markdown renderer for DetailPanel summaries.
//
// The LLM distiller sometimes returns a summary string that
// includes lightweight markdown — mostly `**bold**` for emphasis
// and double-newlines for paragraph breaks. We don't need a full
// markdown parser (we don't render tables, code blocks, links,
// etc. in the summary), and adding `marked` / `markdown-it`
// would be 60-100KB of bundle for what amounts to ~3 regex
// replacements. This module does just enough to make summaries
// feel like "real" prose instead of one wall of text.
//
// The renderer is intentionally safe (no HTML pass-through, no
// URL scheme allowlist to maintain) and the output is split into
// a flat list of text nodes that the caller can interleave with
// <strong> wrappers — React handles the rest.

export type InlineNode =
  | { kind: "text"; text: string }
  | { kind: "strong"; text: string };

const STRONG_RE = /\*\*([^*\n]+)\*\*/g;

/**
 * Parse a string into a flat sequence of inline nodes. The caller
 * (DetailPanel) renders each node as either a plain span or a
 * <strong>, preserving newlines as <br/>.
 */
export function parseInline(raw: string): InlineNode[] {
  if (!raw) return [];
  const nodes: InlineNode[] = [];
  let cursor = 0;
  // Iterate matches in order so the surrounding text is preserved
  // verbatim. A "match" carries the start/end of the bold span;
  // everything between cursor and match.start is plain text.
  for (const m of raw.matchAll(STRONG_RE)) {
    const start = m.index ?? 0;
    if (start > cursor) {
      nodes.push({ kind: "text", text: raw.slice(cursor, start) });
    }
    nodes.push({ kind: "strong", text: m[1] });
    cursor = start + m[0].length;
  }
  if (cursor < raw.length) {
    nodes.push({ kind: "text", text: raw.slice(cursor) });
  }
  return nodes;
}

/**
 * Split a paragraph string on blank lines (double newline) and
 * single newlines (treated as a soft break). Used by DetailPanel
 * to render the summary as 1+ <p> blocks instead of a single
 * squashed line.
 */
export function splitParagraphs(raw: string): string[] {
  if (!raw) return [];
  return raw
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}
