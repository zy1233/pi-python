import { inlineText, type Align, type Block, type Inline } from "../core/markdown.js";
import { isSafeLink } from "../core/protocol.js";

type Child = Node | string | null | undefined | false;

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: { class?: string; text?: string; title?: string; type?: string; disabled?: boolean; dataset?: Record<string, string> } = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (props.class) node.className = props.class;
  if (props.text !== undefined) node.textContent = props.text;
  if (props.title) node.title = props.title;
  if (props.type) node.setAttribute("type", props.type);
  if (props.disabled) node.setAttribute("disabled", "");
  for (const [key, value] of Object.entries(props.dataset ?? {})) node.dataset[key] = value;
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

/** A link. The click goes to the host (see `main.ts`), which opens only http, https and mailto. */
function anchor(url: string, label: Node[]): HTMLAnchorElement {
  const link = el("a", { title: url, dataset: { url } }, ...label);
  link.setAttribute("href", "#");
  return link;
}

function inlineNodes(inline: Inline[]): Node[] {
  return inline.flatMap((part): Node[] => {
    switch (part.t) {
      case "text":
        return [document.createTextNode(part.text)];
      case "code":
        return [el("code", { text: part.text })];
      case "br":
        return [document.createElement("br")];
      case "strong":
        return [el("strong", {}, ...inlineNodes(part.children))];
      case "em":
        return [el("em", {}, ...inlineNodes(part.children))];
      case "del":
        return [el("del", {}, ...inlineNodes(part.children))];
      case "link": {
        const label = inlineNodes(part.children);
        if (isSafeLink(part.url)) return [anchor(part.url, label)];
        // There is no link for an address that the host refuses. The address stays visible.
        return inlineText(part.children) === part.url ? label : [...label, document.createTextNode(` (${part.url})`)];
      }
      case "image": {
        // An image is never loaded: the page has no remote content (CSP), and a model can name any address.
        const name = part.alt !== "" ? part.alt : undefined;
        return isSafeLink(part.url)
          ? [anchor(part.url, [document.createTextNode(name ?? part.url)])]
          : [document.createTextNode(name ?? "image")];
      }
    }
  });
}

function cellNode(tag: "th" | "td", inline: Inline[], align: Align | undefined): HTMLTableCellElement {
  const cell = el(tag, align ? { class: `align-${align}` } : {}, ...inlineNodes(inline));
  if (tag === "th") cell.setAttribute("scope", "col");
  return cell;
}

function blockNodes(block: Block): Node[] {
  switch (block.t) {
    case "p":
      return block.tight ? inlineNodes(block.inline) : [el("p", {}, ...inlineNodes(block.inline))];
    case "heading": {
      const tag = `h${block.level}` as const;
      return [el(tag, {}, ...inlineNodes(block.inline))];
    }
    case "code":
      return [el("pre", { class: "code" }, el("code", { text: block.text }))];
    case "list": {
      const list = el(block.ordered ? "ol" : "ul", {}, ...block.items.map((item) => el("li", {}, ...renderBlocks(item))));
      if (block.ordered && block.start !== 1) list.setAttribute("start", String(block.start));
      return [list];
    }
    case "quote":
      return [el("blockquote", {}, ...renderBlocks(block.blocks))];
    case "table": {
      const head = el("thead", {}, el("tr", {}, ...block.head.map((cell, i) => cellNode("th", cell, block.align[i]))));
      const body = el("tbody", {}, ...block.rows.map((row) => el("tr", {}, ...row.map((cell, i) => cellNode("td", cell, block.align[i])))));
      // The wrapper scrolls a wide table by itself: the side bar is narrow.
      return [el("div", { class: "table-wrap" }, el("table", {}, head, body))];
    }
    case "hr":
      return [document.createElement("hr")];
  }
}

/** Builds DOM nodes from parsed Markdown. It never sets `innerHTML`. */
export function renderBlocks(blocks: Block[]): Node[] {
  return blocks.flatMap(blockNodes);
}
