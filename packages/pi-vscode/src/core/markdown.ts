/**
 * A small Markdown reader for chat text (docs/specs/2026-11-04-vscode-extension-design.md,
 * section 2, rule 5). It makes paragraphs, code blocks, lists, code spans and links, and nothing
 * else. It returns data, not HTML: the webview builds DOM nodes from it with `textContent`, so
 * text from the model can never become markup.
 */

export type Inline =
  | { t: "text"; text: string }
  | { t: "code"; text: string }
  | { t: "link"; text: string; url: string };

export type Block =
  | { t: "p"; inline: Inline[] }
  | { t: "code"; lang: string; text: string }
  | { t: "list"; ordered: boolean; items: Inline[][] };

const FENCE = /^\s{0,3}```\s*([\w+#.-]*)\s*$/;
const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const NUMBERED = /^\s{0,3}\d{1,9}[.)]\s+(.*)$/;
const URL_PATTERN = /^https?:\/\/[^\s<>`]+/;
const TRAILING_PUNCTUATION = /[.,;:!?)\]]+$/;

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: Inline[][] } | undefined;

  const flushParagraph = () => {
    if (paragraph.length > 0) blocks.push({ t: "p", inline: parseInline(paragraph.join("\n")) });
    paragraph = [];
  };
  const flushList = () => {
    if (list) blocks.push({ t: "list", ordered: list.ordered, items: list.items });
    list = undefined;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    const fence = FENCE.exec(line);
    if (fence) {
      flushParagraph();
      flushList();
      const code: string[] = [];
      i++;
      // An unclosed fence runs to the end: the text is still streaming.
      while (i < lines.length && !FENCE.test(lines[i] ?? "")) code.push(lines[i++] ?? "");
      blocks.push({ t: "code", lang: fence[1] ?? "", text: code.join("\n") });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      flushParagraph();
      const ordered = numbered !== null;
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push(parseInline(item[1] ?? ""));
      continue;
    }
    if (line.trim() === "") {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
  return blocks;
}

export function parseInline(source: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const pushText = () => {
    if (text !== "") out.push({ t: "text", text });
    text = "";
  };
  let i = 0;
  while (i < source.length) {
    const rest = source.slice(i);
    const ch = source[i];
    if (ch === "`") {
      const end = source.indexOf("`", i + 1);
      if (end > i + 1 && !source.slice(i + 1, end).includes("\n")) {
        pushText();
        out.push({ t: "code", text: source.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    } else if (ch === "[") {
      const link = /^\[([^\]\n]+)\]\(([^()\s]+)\)/.exec(rest);
      if (link) {
        pushText();
        out.push({ t: "link", text: link[1] ?? "", url: link[2] ?? "" });
        i += link[0].length;
        continue;
      }
    } else if (ch === "h") {
      const bare = URL_PATTERN.exec(rest);
      const previous = i > 0 ? source[i - 1] : " ";
      if (bare && previous !== undefined && /[\s(]/.test(previous)) {
        const url = bare[0].replace(TRAILING_PUNCTUATION, "");
        if (url.length > "https://".length) {
          pushText();
          out.push({ t: "link", text: url, url });
          i += url.length;
          continue;
        }
      }
    }
    text += ch;
    i++;
  }
  pushText();
  return out;
}
