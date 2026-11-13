import { describe, expect, it } from "vitest";

import { inlineText, parseMarkdown, type Block, type Inline } from "../src/core/markdown.js";

const text = (value: string): Inline => ({ t: "text", text: value });
const p = (...inline: Inline[]): Block => ({ t: "p", inline });
const tight = (...inline: Inline[]): Block => ({ t: "p", inline, tight: true });

/** The inline parts of the first paragraph of `source`. */
function inlineOf(source: string): Inline[] {
  const first = parseMarkdown(source)[0];
  if (first?.t !== "p") throw new Error(`not a paragraph: ${JSON.stringify(first)}`);
  return first.inline;
}

describe("parseMarkdown: blocks", () => {
  it("makes paragraphs from blank-line separated text, and a single newline is a line break", () => {
    expect(parseMarkdown("one\ntwo\n\nthree")).toEqual([p(text("one"), { t: "br" }, text("two")), p(text("three"))]);
  });

  it("reads CRLF and a hard break (two spaces, or a backslash) like a newline", () => {
    expect(parseMarkdown("a\r\nb")).toEqual([p(text("a"), { t: "br" }, text("b"))]);
    expect(parseMarkdown("a  \nb\\\nc")).toEqual([p(text("a"), { t: "br" }, text("b"), { t: "br" }, text("c"))]);
  });

  it("makes a code block with its language, and keeps markup inside as text", () => {
    const blocks = parseMarkdown("before\n```ts\nconst a = `x` <b>;\n```\nafter");
    expect(blocks).toEqual([
      p(text("before")),
      { t: "code", lang: "ts", text: "const a = `x` <b>;" },
      p(text("after")),
    ]);
  });

  it("takes the first word of the info string as the language, and an indented block has none", () => {
    expect(parseMarkdown("```python title=x\npass\n```")).toEqual([{ t: "code", lang: "python", text: "pass" }]);
    expect(parseMarkdown("text\n\n    indented\n")).toEqual([p(text("text")), { t: "code", lang: "", text: "indented" }]);
  });

  it("runs an unclosed fence to the end (the text is still streaming)", () => {
    expect(parseMarkdown("```\nline 1\nline 2")).toEqual([{ t: "code", lang: "", text: "line 1\nline 2" }]);
  });

  it("makes lists, and splits an unordered list from an ordered one", () => {
    expect(parseMarkdown("- a\n- b\n1. c\n2. d")).toEqual([
      { t: "list", ordered: false, start: 1, items: [[tight(text("a"))], [tight(text("b"))]] },
      { t: "list", ordered: true, start: 1, items: [[tight(text("c"))], [tight(text("d"))]] },
    ]);
  });

  it("keeps the start number of an ordered list", () => {
    expect(parseMarkdown("3. x\n4. y")).toEqual([
      { t: "list", ordered: true, start: 3, items: [[tight(text("x"))], [tight(text("y"))]] },
    ]);
  });

  it("makes nested lists", () => {
    expect(parseMarkdown("- a\n  - b\n  - c\n- d")).toEqual([
      {
        t: "list",
        ordered: false,
        start: 1,
        items: [
          [
            tight(text("a")),
            { t: "list", ordered: false, start: 1, items: [[tight(text("b"))], [tight(text("c"))]] },
          ],
          [tight(text("d"))],
        ],
      },
    ]);
  });

  it("marks a paragraph in a tight list only: a loose list keeps real paragraphs", () => {
    expect(parseMarkdown("- a\n\n- b")).toEqual([
      { t: "list", ordered: false, start: 1, items: [[p(text("a"))], [p(text("b"))]] },
    ]);
  });

  it("puts a code block inside a list item", () => {
    expect(parseMarkdown("1. run:\n   ```sh\n   ls\n   ```")).toEqual([
      {
        t: "list",
        ordered: true,
        start: 1,
        items: [[tight(text("run:")), { t: "code", lang: "sh", text: "ls" }]],
      },
    ]);
  });

  it("makes headings of six levels, with inline markup inside, and setext headings", () => {
    const levels = parseMarkdown("# 1\n## 2\n### 3\n#### 4\n##### 5\n###### 6").map((b) => (b.t === "heading" ? b.level : 0));
    expect(levels).toEqual([1, 2, 3, 4, 5, 6]);
    expect(parseMarkdown("## T `c`")).toEqual([{ t: "heading", level: 2, inline: [text("T "), { t: "code", text: "c" }] }]);
    expect(parseMarkdown("Title\n=====\ntext\n---")).toEqual([
      { t: "heading", level: 1, inline: [text("Title")] },
      { t: "heading", level: 2, inline: [text("text")] },
    ]);
  });

  it("makes a quote that holds other blocks, and a rule", () => {
    expect(parseMarkdown("> q\n> - i\n\n---")).toEqual([
      {
        t: "quote",
        blocks: [p(text("q")), { t: "list", ordered: false, start: 1, items: [[tight(text("i"))]] }],
      },
      { t: "hr" },
    ]);
  });

  it("makes a table with the alignment of each column; a short row is padded and a long row is cut", () => {
    const [table] = parseMarkdown("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 |\n| x | y | z | w |");
    expect(table).toEqual({
      t: "table",
      align: ["left", "center", "right"],
      head: [[text("a")], [text("b")], [text("c")]],
      rows: [
        [[text("1")], [text("2")], []],
        [[text("x")], [text("y")], [text("z")]],
      ],
    });
  });

  it("puts inline markup in a table cell", () => {
    const [table] = parseMarkdown("| a |\n|---|\n| `x` **y** |");
    expect(table).toMatchObject({
      t: "table",
      align: [undefined],
      rows: [[[{ t: "code", text: "x" }, text(" "), { t: "strong", children: [text("y")] }]]],
    });
  });

  it("shows a table header alone as a paragraph until the separator row arrives (streaming)", () => {
    expect(parseMarkdown("| a | b |")).toEqual([p(text("| a | b |"))]);
    expect(parseMarkdown("| a | b |\n|---|---|")[0]).toMatchObject({ t: "table" });
  });

  it("gives an empty list for empty input", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown("\n\n")).toEqual([]);
  });
});

describe("parseMarkdown: inline", () => {
  it("reads code spans, bold, italic and strikethrough, also inside each other", () => {
    expect(inlineOf("**a *b* c** ~~d~~ _e_ `f`")).toEqual([
      { t: "strong", children: [text("a "), { t: "em", children: [text("b")] }, text(" c")] },
      text(" "),
      { t: "del", children: [text("d")] },
      text(" "),
      { t: "em", children: [text("e")] },
      text(" "),
      { t: "code", text: "f" },
    ]);
  });

  it("leaves a lone or unmatched marker as text", () => {
    expect(inlineOf("a ` b [c ** d")).toEqual([text("a ` b [c ** d")]);
    expect(inlineOf("2 * 3 * 4 = 24")).toEqual([text("2 * 3 * 4 = 24")]);
  });

  it("reads a link whose text has markup", () => {
    expect(inlineOf("run `ls -la` and see [the **docs**](https://zycode.dev/docs).")).toEqual([
      text("run "),
      { t: "code", text: "ls -la" },
      text(" and see "),
      { t: "link", url: "https://zycode.dev/docs", children: [text("the "), { t: "strong", children: [text("docs")] }] },
      text("."),
    ]);
  });

  it("links a bare URL without its closing punctuation, also next to Chinese punctuation", () => {
    const link = (url: string): Inline => ({ t: "link", url, children: [text(url)] });
    expect(inlineOf("see https://example.com/a, ok")).toEqual([text("see "), link("https://example.com/a"), text(", ok")]);
    expect(inlineOf("见 https://a.io/x，然后（https://b.io/y）。")).toEqual([
      text("见 "),
      link("https://a.io/x"),
      text("，然后（"),
      link("https://b.io/y"),
      text("）。"),
    ]);
  });

  it("links <https://...> and an email in angle brackets (as mailto)", () => {
    expect(inlineOf("<https://example.com/a> <a@b.co>")).toEqual([
      { t: "link", url: "https://example.com/a", children: [text("https://example.com/a")] },
      text(" "),
      { t: "link", url: "mailto:a@b.co", children: [text("a@b.co")] },
    ]);
  });

  it("does not link a file name, a bare www address, a bare email, ftp:, or a URL in the middle of a word", () => {
    for (const source of [
      "open main.py or foo.sh",
      "see www.example.com",
      "write to a@b.co",
      "get ftp://x.org/f",
      "load //cdn.example.com/x",
      "xhttps://a.b",
    ]) {
      expect(inlineOf(source), source).toEqual([text(source)]);
    }
  });

  it("keeps the address of a link as data (the webview decides if it can open), but not javascript:, file: or data:", () => {
    expect(inlineOf("[y](vscode://file/etc)")).toEqual([
      { t: "link", url: "vscode://file/etc", children: [text("y")] },
    ]);
    for (const source of ["[x](javascript:alert(1))", "[z](file:///etc/passwd)", "[d](data:text/html,hi)"]) {
      expect(inlineOf(source), source).toEqual([text(source)]);
    }
  });

  it("reads an image as an image part, with its description, and loads nothing", () => {
    expect(inlineOf("![a *b*](https://e.com/a.png)![](https://e.com/b.png)")).toEqual([
      { t: "image", alt: "a b", url: "https://e.com/a.png" },
      { t: "image", alt: "", url: "https://e.com/b.png" },
    ]);
  });

  it("keeps HTML as text, and decodes an entity to text, never to markup", () => {
    expect(inlineOf("text <b>b</b> &amp; &lt;i&gt;")).toEqual([text("text <b>b</b> & <i>")]);
    expect(parseMarkdown("<script>alert(1)</script>\n\n<div>x</div>\n\n<img src=x onerror=alert(1)>")).toEqual([
      p(text("<script>alert(1)</script>")),
      p(text("<div>x</div>")),
      p(text("<img src=x onerror=alert(1)>")),
    ]);
  });

  it("makes bold next to Chinese punctuation and letters (CommonMark alone leaves the ** as text)", () => {
    const bold = (value: string): Inline => ({ t: "strong", children: [text(value)] });
    expect(inlineOf("**重要：**后面")).toEqual([bold("重要："), text("后面")]);
    expect(inlineOf("**（注）**后面")).toEqual([bold("（注）"), text("后面")]);
    expect(inlineOf("**“引用”**后面")).toEqual([bold("“引用”"), text("后面")]);
    expect(inlineOf("这是**行为改动**，见下")).toEqual([text("这是"), bold("行为改动"), text("，见下")]);
  });
});

describe("inlineText", () => {
  it("gives the plain text of formatted parts, a line break as a space, and an image as its description", () => {
    const source = "**a** *b* `c` [d](https://x.io) ![e](https://x.io/e.png)\nf";
    expect(inlineText(inlineOf(source))).toBe("a b c d e f");
  });
});

/** A walk that checks the shape of what the reader gives. A part that breaks it would break the webview. */
function checkInline(parts: Inline[]): void {
  for (const part of parts) {
    switch (part.t) {
      case "text":
      case "code":
        expect(typeof part.text).toBe("string");
        break;
      case "br":
        break;
      case "image":
        expect(typeof part.alt + typeof part.url).toBe("stringstring");
        break;
      case "link":
        expect(typeof part.url).toBe("string");
        checkInline(part.children);
        break;
      case "strong":
      case "em":
      case "del":
        checkInline(part.children);
        break;
      default:
        throw new Error(`unknown inline part: ${JSON.stringify(part)}`);
    }
  }
}

function checkBlocks(blocks: Block[]): void {
  for (const block of blocks) {
    switch (block.t) {
      case "p":
      case "heading":
        checkInline(block.inline);
        break;
      case "code":
        expect(typeof block.text).toBe("string");
        break;
      case "hr":
        break;
      case "quote":
        checkBlocks(block.blocks);
        break;
      case "list":
        expect(Number.isFinite(block.start)).toBe(true);
        for (const item of block.items) checkBlocks(item);
        break;
      case "table":
        expect(block.align).toHaveLength(block.head.length);
        for (const row of block.rows) expect(row).toHaveLength(block.head.length);
        for (const cell of [...block.head, ...block.rows.flat()]) checkInline(cell);
        break;
      default:
        throw new Error(`unknown block: ${JSON.stringify(block)}`);
    }
  }
}

describe("parseMarkdown: hostile and random input", () => {
  it("nests marks to a bounded depth and keeps the text", () => {
    // Not recursive: the test must fail on an assertion, not on the stack of its own helper.
    const depth = (inline: readonly Inline[]): number => {
      let deepest = 0;
      const todo: Array<[readonly Inline[], number]> = [[inline, 0]];
      for (let item = todo.pop(); item; item = todo.pop()) {
        deepest = Math.max(deepest, item[1]);
        for (const part of item[0]) if ("children" in part) todo.push([part.children, item[1] + 1]);
      }
      return deepest;
    };
    for (const [open, close] of [["*a ", "a* "], ["**a ", "a** "], ["~~a ", "a~~ "]] as const) {
      const [block] = parseMarkdown(open.repeat(5000) + close.repeat(5000));
      expect(block?.t).toBe("p");
      const inline = (block as Extract<Block, { t: "p" }>).inline;
      expect(depth(inline)).toBeLessThanOrEqual(33);
      expect(inlineText(inline).replace(/\s/g, "")).toBe("a".repeat(10000));
    }
  });

  it("returns for text that is built to be slow or deep, and the result has a valid shape", () => {
    const inputs = [
      "[".repeat(20000),
      "*".repeat(50000),
      "`".repeat(30000),
      "![".repeat(10000),
      "[a](".repeat(5000),
      "<".repeat(20000),
      "> ".repeat(2000) + "x",
      "- ".repeat(2000) + "x",
      Array.from({ length: 200 }, (_, i) => `${"  ".repeat(i)}- item`).join("\n"),
      "|a|b|\n|-|-|\n" + "|x|y|\n".repeat(3000),
      "_a ".repeat(20000),
      "**a_ ".repeat(10000),
    ];
    for (const input of inputs) checkBlocks(parseMarkdown(input));
  });

  it("returns a valid shape for 3000 random mixes of Markdown marks (a fixed seed, so a failure repeats)", () => {
    const marks = ["*", "**", "_", "~~", "`", "```", "[", "]", "(", ")", "![", "<", ">", "&", "\n", "\n\n", " ", "  ", "- ", "1. ", "# ", "> ", "|", "---", "|---|", "http://a.b/", "x", "中", "：", "\\", "<script>", "&amp;", "\t", "    "];
    let seed = 20261112;
    const random = () => {
      // mulberry32
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    for (let n = 0; n < 3000; n++) {
      let doc = "";
      const length = 1 + Math.floor(random() * 40);
      for (let i = 0; i < length; i++) doc += marks[Math.floor(random() * marks.length)];
      checkBlocks(parseMarkdown(doc));
    }
  });
});
