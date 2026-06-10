import { describe, it, expect } from "vitest";
import { parseInline, splitParagraphs } from "../inline-markdown";

describe("parseInline", () => {
  it("returns plain text unchanged when no markers are present", () => {
    expect(parseInline("hello world")).toEqual([
      { kind: "text", text: "hello world" },
    ]);
  });

  it("converts **bold** spans into strong nodes", () => {
    expect(parseInline("foo **bar** baz")).toEqual([
      { kind: "text", text: "foo " },
      { kind: "strong", text: "bar" },
      { kind: "text", text: " baz" },
    ]);
  });

  it("preserves multiple bold spans in source order", () => {
    const out = parseInline("**one** middle **two** end");
    expect(out).toEqual([
      { kind: "strong", text: "one" },
      { kind: "text", text: " middle " },
      { kind: "strong", text: "two" },
      { kind: "text", text: " end" },
    ]);
  });

  it("leaves unmatched asterisks as literal text", () => {
    // A single * is not a bold marker; we don't italic here
    // (italic is rare in LLM summaries and would add a regex).
    expect(parseInline("5 * 3 = 15")).toEqual([
      { kind: "text", text: "5 * 3 = 15" },
    ]);
  });

  it("ignores ** markers that span a newline", () => {
    // The distiller occasionally emits "**broken" without a
    // closing pair — we leave it as text rather than render
    // a half-baked strong.
    expect(parseInline("**oops\nmore text")).toEqual([
      { kind: "text", text: "**oops\nmore text" },
    ]);
  });

  it("returns an empty list for an empty string", () => {
    expect(parseInline("")).toEqual([]);
  });
});

describe("splitParagraphs", () => {
  it("splits on double newlines", () => {
    expect(splitParagraphs("first.\n\nsecond.")).toEqual([
      "first.",
      "second.",
    ]);
  });

  it("trims whitespace and drops empty paragraphs", () => {
    expect(splitParagraphs("  a  \n\n\n\n  b  ")).toEqual(["a", "b"]);
  });

  it("returns a single-element list when there are no breaks", () => {
    expect(splitParagraphs("just one paragraph")).toEqual([
      "just one paragraph",
    ]);
  });

  it("returns an empty list for an empty string", () => {
    expect(splitParagraphs("")).toEqual([]);
  });
});
