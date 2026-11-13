/**
 * The Markdown reader for chat text (docs/specs/2026-11-04-vscode-extension-design.md, section 2,
 * rule 5, and decision D34).
 *
 * `markdown-it` only reads the text here. It gives a list of tokens, and this file turns the tokens
 * into plain data (`Block[]`, `Inline[]`). Nothing asks `markdown-it` for HTML. The webview builds
 * DOM nodes from the data with `textContent` (src/webview/dom.ts), so text from the model can never
 * become markup.
 */
import MarkdownIt, { type Token } from "markdown-it";
import cjkFriendly from "markdown-it-cjk-friendly";

export type Align = "left" | "center" | "right";
export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

export type Inline =
  | { t: "text"; text: string }
  | { t: "code"; text: string }
  | { t: "br" }
  | { t: "strong"; children: Inline[] }
  | { t: "em"; children: Inline[] }
  | { t: "del"; children: Inline[] }
  | { t: "link"; url: string; children: Inline[] }
  /** An image is never loaded. The webview shows it as a link (or as its description). */
  | { t: "image"; alt: string; url: string };

export type Block =
  /** `tight` is set for a paragraph in a tight list item: the webview adds no `<p>` for it. */
  | { t: "p"; inline: Inline[]; tight?: boolean }
  | { t: "heading"; level: HeadingLevel; inline: Inline[] }
  | { t: "code"; lang: string; text: string }
  | { t: "list"; ordered: boolean; start: number; items: Block[][] }
  | { t: "quote"; blocks: Block[] }
  | { t: "table"; align: (Align | undefined)[]; head: Inline[][]; rows: Inline[][][] }
  | { t: "hr" };

// - `html` is off (it is also the default): a tag in the text stays text.
// - `linkify` makes a link of `https://...`, `http://...` and `mailto:...`. The fuzzy kinds are off:
//   `main.py` is not a link (`.py` is a country domain), and a bare `www.x.com` or `a@b.co` is not
//   one either. The schemes `ftp:` and `//` are removed: the webview opens only http, https and mailto.
// - `markdown-it-cjk-friendly`: without it, `**重要：**后面` keeps its `**` as text. CommonMark does not
//   close bold next to a Chinese letter after a punctuation mark.
const md = new MarkdownIt({ html: false, linkify: true, typographer: false }).use(cjkFriendly);
md.linkify.set({ fuzzyLink: false, fuzzyEmail: false, fuzzyIP: false });
md.linkify.add("ftp:", null);
md.linkify.add("//", null);

export function parseMarkdown(source: string): Block[] {
  return readBlocks(md.parse(source, {}), 0, "").blocks;
}

/** The plain text of some inline parts, without markup. A line break is a space. */
export function inlineText(inline: readonly Inline[]): string {
  let out = "";
  for (const part of inline) {
    switch (part.t) {
      case "text":
      case "code":
        out += part.text;
        break;
      case "br":
        out += " ";
        break;
      case "image":
        out += part.alt;
        break;
      default:
        out += inlineText(part.children);
    }
  }
  return out;
}

/**
 * Reads blocks from `start` up to the token of type `closeType` (an empty string: to the end).
 * `next` is the index after that token. A token that this reader does not know is skipped.
 */
function readBlocks(tokens: readonly Token[], start: number, closeType: string): { blocks: Block[]; next: number } {
  const blocks: Block[] = [];
  let i = start;
  while (i < tokens.length) {
    const token = tokens[i];
    if (!token) break;
    if (closeType !== "" && token.type === closeType) return { blocks, next: i + 1 };
    switch (token.type) {
      case "paragraph_open": {
        const inline = readInline(tokens[i + 1]);
        blocks.push(token.hidden ? { t: "p", inline, tight: true } : { t: "p", inline });
        i = after(tokens, i, "paragraph_close");
        break;
      }
      case "heading_open":
        blocks.push({ t: "heading", level: headingLevel(token.tag), inline: readInline(tokens[i + 1]) });
        i = after(tokens, i, "heading_close");
        break;
      case "fence":
        blocks.push({ t: "code", lang: token.info.trim().split(/\s+/, 1)[0] ?? "", text: withoutFinalNewline(token.content) });
        i++;
        break;
      case "code_block":
        blocks.push({ t: "code", lang: "", text: withoutFinalNewline(token.content) });
        i++;
        break;
      case "hr":
        blocks.push({ t: "hr" });
        i++;
        break;
      case "blockquote_open": {
        const inner = readBlocks(tokens, i + 1, "blockquote_close");
        blocks.push({ t: "quote", blocks: inner.blocks });
        i = inner.next;
        break;
      }
      case "bullet_list_open":
      case "ordered_list_open": {
        const list = readList(tokens, i);
        blocks.push(list.block);
        i = list.next;
        break;
      }
      case "table_open": {
        const table = readTable(tokens, i);
        blocks.push(table.block);
        i = table.next;
        break;
      }
      case "html_block":
        // Not made while `html` is off. If it ever is, it is text.
        blocks.push({ t: "p", inline: [{ t: "text", text: withoutFinalNewline(token.content) }] });
        i++;
        break;
      default:
        i++;
    }
  }
  return { blocks, next: i };
}

function readList(tokens: readonly Token[], open: number): { block: Block; next: number } {
  const first = tokens[open];
  const ordered = first?.type === "ordered_list_open";
  const closeType = ordered ? "ordered_list_close" : "bullet_list_close";
  const start = ordered && first ? Number(attr(first, "start") || 1) : 1;
  const items: Block[][] = [];
  let i = open + 1;
  while (i < tokens.length) {
    const token = tokens[i];
    if (!token) break;
    if (token.type === closeType) {
      i++;
      break;
    }
    if (token.type === "list_item_open") {
      const item = readBlocks(tokens, i + 1, "list_item_close");
      items.push(item.blocks);
      i = item.next;
    } else {
      i++;
    }
  }
  return { block: { t: "list", ordered, start: Number.isFinite(start) ? start : 1, items }, next: i };
}

function readTable(tokens: readonly Token[], open: number): { block: Block; next: number } {
  let head: Inline[][] = [];
  const rows: Inline[][][] = [];
  const align: (Align | undefined)[] = [];
  let row: Inline[][] | undefined;
  let inHead = false;
  let i = open + 1;
  for (; i < tokens.length; i++) {
    const token = tokens[i];
    if (!token) break;
    if (token.type === "table_close") {
      i++;
      break;
    }
    switch (token.type) {
      case "thead_open":
        inHead = true;
        break;
      case "thead_close":
        inHead = false;
        break;
      case "tr_open":
        row = [];
        break;
      case "tr_close":
        if (row) {
          if (inHead) head = row;
          else rows.push(row);
        }
        row = undefined;
        break;
      case "th_open":
        if (inHead) align.push(alignOf(token));
        break;
      case "inline":
        row?.push(readInline(token));
        break;
    }
  }
  return { block: { t: "table", align, head, rows }, next: i };
}

/** The index after the first token of type `closeType` that follows `from`. */
function after(tokens: readonly Token[], from: number, closeType: string): number {
  let i = from + 1;
  while (i < tokens.length && tokens[i]?.type !== closeType) i++;
  return i + 1;
}

function headingLevel(tag: string): HeadingLevel {
  const level = Number(tag.slice(1));
  return level >= 1 && level <= 6 ? (level as HeadingLevel) : 1;
}

/** An attribute of a token as a string (the typings of `markdown-it` allow a number). */
function attr(token: Token, name: string): string {
  return String(token.attrGet(name) ?? "");
}

function alignOf(token: Token): Align | undefined {
  const match = /^text-align:(left|center|right)$/.exec(attr(token, "style"));
  return match ? (match[1] as Align) : undefined;
}

function withoutFinalNewline(text: string): string {
  return text.endsWith("\n") ? text.slice(0, -1) : text;
}

/** Marks nest only this deep. A deeper mark is dropped and its text stays, so a hostile text cannot make a deep DOM tree. */
const MAX_INLINE_DEPTH = 32;

type Frame =
  | { kind: "strong" | "em" | "del"; children: Inline[]; flat?: true }
  | { kind: "link"; url: string; children: Inline[]; flat?: true };

/** Reads the children of an `inline` token. A single newline is a line break, as in most chat apps. */
function readInline(token: Token | undefined): Inline[] {
  const root: Inline[] = [];
  const stack: Frame[] = [];
  const here = (): Inline[] => stack[stack.length - 1]?.children ?? root;
  const pushText = (text: string) => {
    if (text === "") return;
    const target = here();
    const last = target[target.length - 1];
    if (last?.t === "text") last.text += text;
    else target.push({ t: "text", text });
  };
  const open = (frame: Frame) => {
    stack.push(stack.length >= MAX_INLINE_DEPTH ? { ...frame, children: here(), flat: true } : frame);
  };
  const closeTop = () => {
    const frame = stack.pop();
    if (!frame || frame.flat) return;
    if (frame.kind === "link") here().push({ t: "link", url: frame.url, children: frame.children });
    else if (frame.kind === "strong") here().push({ t: "strong", children: frame.children });
    else if (frame.kind === "em") here().push({ t: "em", children: frame.children });
    else here().push({ t: "del", children: frame.children });
  };
  const close = (kind: Frame["kind"]) => {
    if (stack[stack.length - 1]?.kind === kind) closeTop();
  };

  for (const child of token?.children ?? []) {
    switch (child.type) {
      case "text":
        pushText(child.content);
        break;
      case "code_inline":
        here().push({ t: "code", text: child.content });
        break;
      case "softbreak":
      case "hardbreak":
        here().push({ t: "br" });
        break;
      case "strong_open":
        open({ kind: "strong", children: [] });
        break;
      case "strong_close":
        close("strong");
        break;
      case "em_open":
        open({ kind: "em", children: [] });
        break;
      case "em_close":
        close("em");
        break;
      case "s_open":
        open({ kind: "del", children: [] });
        break;
      case "s_close":
        close("del");
        break;
      case "link_open":
        open({ kind: "link", url: attr(child, "href"), children: [] });
        break;
      case "link_close":
        close("link");
        break;
      case "image":
        here().push({ t: "image", alt: plainText(child.children) || child.content, url: attr(child, "src") });
        break;
      default:
        // `html_inline` is not made while `html` is off. If it ever is, it is text.
        pushText(child.content);
    }
  }
  // `markdown-it` always closes what it opens. A frame that is still open is closed here, not lost.
  while (stack.length > 0) closeTop();
  return root;
}

function plainText(tokens: readonly Token[] | null): string {
  let out = "";
  for (const token of tokens ?? []) {
    if (token.type === "text" || token.type === "code_inline") out += token.content;
    else if (token.type === "softbreak" || token.type === "hardbreak") out += " ";
    else if (token.type === "image") out += plainText(token.children);
  }
  return out;
}
