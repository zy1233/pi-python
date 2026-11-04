import { describe, expect, it } from "vitest";

import { parseInline, parseMarkdown } from "../src/core/markdown.js";

describe("parseMarkdown", () => {
  it("makes paragraphs from blank-line separated text and keeps single newlines", () => {
    expect(parseMarkdown("one\ntwo\n\nthree")).toEqual([
      { t: "p", inline: [{ t: "text", text: "one\ntwo" }] },
      { t: "p", inline: [{ t: "text", text: "three" }] },
    ]);
  });

  it("makes a code block with its language, and keeps markup inside as text", () => {
    const blocks = parseMarkdown("before\n```ts\nconst a = `x` <b>;\n```\nafter");
    expect(blocks).toEqual([
      { t: "p", inline: [{ t: "text", text: "before" }] },
      { t: "code", lang: "ts", text: "const a = `x` <b>;" },
      { t: "p", inline: [{ t: "text", text: "after" }] },
    ]);
  });

  it("runs an unclosed fence to the end (the text is still streaming)", () => {
    expect(parseMarkdown("```\nline 1\nline 2")).toEqual([{ t: "code", lang: "", text: "line 1\nline 2" }]);
  });

  it("makes lists, and splits an unordered list from an ordered one", () => {
    expect(parseMarkdown("- a\n- b\n1. c\n2. d")).toEqual([
      { t: "list", ordered: false, items: [[{ t: "text", text: "a" }], [{ t: "text", text: "b" }]] },
      { t: "list", ordered: true, items: [[{ t: "text", text: "c" }], [{ t: "text", text: "d" }]] },
    ]);
  });

  it("does not make headings, bold or HTML: they stay text", () => {
    const blocks = parseMarkdown("# title\n**bold** <script>alert(1)</script>");
    expect(blocks).toEqual([
      { t: "p", inline: [{ t: "text", text: "# title\n**bold** <script>alert(1)</script>" }] },
    ]);
  });

  it("gives an empty list for empty input", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown("\n\n")).toEqual([]);
  });
});

describe("parseInline", () => {
  it("reads code spans and links", () => {
    expect(parseInline("run `ls -la` and see [the docs](https://zycode.dev/docs).")).toEqual([
      { t: "text", text: "run " },
      { t: "code", text: "ls -la" },
      { t: "text", text: " and see " },
      { t: "link", text: "the docs", url: "https://zycode.dev/docs" },
      { t: "text", text: "." },
    ]);
  });

  it("links a bare URL without its closing punctuation", () => {
    expect(parseInline("see https://example.com/a, ok")).toEqual([
      { t: "text", text: "see " },
      { t: "link", text: "https://example.com/a", url: "https://example.com/a" },
      { t: "text", text: ", ok" },
    ]);
  });

  it("keeps an unmatched backtick or bracket as text", () => {
    expect(parseInline("a ` b [c")).toEqual([{ t: "text", text: "a ` b [c" }]);
  });

  it("does not make a link of a URL in the middle of a word", () => {
    expect(parseInline("xhttps://a.b")).toEqual([{ t: "text", text: "xhttps://a.b" }]);
  });

  it("keeps the url of a link as data: the host decides if it is safe", () => {
    expect(parseInline("[x](javascript:alert(1))")[0]).toMatchObject({ t: "text" });
    expect(parseInline("[x](file:///etc/passwd)")).toEqual([{ t: "link", text: "x", url: "file:///etc/passwd" }]);
  });
});
