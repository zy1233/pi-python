import type { Block, Inline } from "../core/markdown.js";
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

function inlineNodes(inline: Inline[]): Node[] {
  return inline.map((part) => {
    if (part.t === "code") return el("code", { text: part.text });
    if (part.t === "link") {
      if (!isSafeLink(part.url)) return document.createTextNode(`${part.text} (${part.url})`);
      const link = el("a", { text: part.text, title: part.url, dataset: { url: part.url } });
      link.setAttribute("href", "#");
      return link;
    }
    return document.createTextNode(part.text);
  });
}

/** Builds DOM nodes from parsed Markdown. It never sets `innerHTML`. */
export function renderBlocks(blocks: Block[]): Node[] {
  return blocks.map((block) => {
    if (block.t === "code") {
      return el("pre", { class: "code" }, el("code", { text: block.text }));
    }
    if (block.t === "list") {
      return el(
        block.ordered ? "ol" : "ul",
        {},
        ...block.items.map((item) => el("li", {}, ...inlineNodes(item))),
      );
    }
    return el("p", {}, ...inlineNodes(block.inline));
  });
}
