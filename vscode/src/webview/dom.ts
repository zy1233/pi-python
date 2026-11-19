import { cleanCodeLine, codeLanguageLabel } from "../core/copy.js";
import { inlineText, type Align, type Block, type Inline } from "../core/markdown.js";
import { isSafeLink } from "../core/protocol.js";

type Child = Node | string | null | undefined | false;

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: {
    id?: string;
    for?: string;
    class?: string;
    text?: string;
    title?: string;
    type?: string;
    disabled?: boolean;
    hidden?: boolean;
    ariaLabel?: string;
    ariaHidden?: boolean;
    ariaExpanded?: string;
    ariaControls?: string;
    ariaSelected?: string;
    ariaAutocomplete?: string;
    ariaActiveDescendant?: string;
    role?: string;
    dataset?: Record<string, string>;
  } = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (props.id) node.id = props.id;
  if (props.for) node.setAttribute("for", props.for);
  if (props.class) node.className = props.class;
  if (props.text !== undefined) node.textContent = props.text;
  if (props.title) node.title = props.title;
  if (props.type) node.setAttribute("type", props.type);
  if (props.disabled) node.setAttribute("disabled", "");
  if (props.hidden) node.hidden = true;
  if (props.ariaLabel) node.setAttribute("aria-label", props.ariaLabel);
  if (props.ariaHidden) node.setAttribute("aria-hidden", "true");
  if (props.ariaExpanded !== undefined) node.setAttribute("aria-expanded", props.ariaExpanded);
  if (props.ariaControls) node.setAttribute("aria-controls", props.ariaControls);
  if (props.ariaSelected !== undefined) node.setAttribute("aria-selected", props.ariaSelected);
  if (props.ariaAutocomplete) node.setAttribute("aria-autocomplete", props.ariaAutocomplete);
  if (props.ariaActiveDescendant) node.setAttribute("aria-activedescendant", props.ariaActiveDescendant);
  if (props.role) node.setAttribute("role", props.role);
  for (const [key, value] of Object.entries(props.dataset ?? {})) node.dataset[key] = value;
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

/** Splits `text` into text nodes and `<mark class="match">` spans using `textContent` only. */
export function highlightedText(text: string, indices?: readonly number[]): Node[] {
  if (!indices || indices.length === 0 || text === "") {
    return [document.createTextNode(text)];
  }
  const matched = new Set<number>();
  for (const idx of indices) {
    if (idx >= 0 && idx < text.length) matched.add(idx);
  }
  if (matched.size === 0) {
    return [document.createTextNode(text)];
  }
  const nodes: Node[] = [];
  let i = 0;
  while (i < text.length) {
    const isHit = matched.has(i);
    let j = i + 1;
    while (j < text.length && matched.has(j) === isHit) j++;
    const slice = text.slice(i, j);
    nodes.push(isHit ? el("mark", { class: "match", text: slice }) : document.createTextNode(slice));
    i = j;
  }
  return nodes;
}

/**
 * Icon + CSS-rendered label copy button. It intentionally contains no text nodes so `textContent`
 * of surrounding prose/code blocks stays clean and drag-selection never captures button labels.
 */
export function copyButton(text: string, ariaLabel = "Copy", extraClass = "", label = "Copy"): HTMLButtonElement {
  return el(
    "button",
    {
      type: "button",
      class: ["copy-btn", extraClass].filter(Boolean).join(" "),
      ariaLabel,
      title: ariaLabel,
      dataset: { copy: text, label },
    },
    el("span", { class: "copy-icon", ariaHidden: true }),
  );
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
        return [el("code", { class: "inline-code", text: part.text, title: "Click to copy", dataset: { copy: part.text } })];
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
    case "code": {
      const langLabel = codeLanguageLabel(block.lang);
      const copyAria = block.lang ? `Copy ${block.lang} code` : "Copy code";
      const head = el(
        "div",
        { class: "code-head" },
        el("span", { class: "code-lang", dataset: { lang: langLabel } }),
        copyButton(block.text, copyAria, "code-copy", "Copy"),
      );
      const codeEl = el("code");
      const lines = block.text.split("\n");
      lines.forEach((line, index) => {
        if (index > 0) codeEl.append(document.createTextNode("\n"));
        const lineEl = el("span", { class: "code-line" }, document.createTextNode(line));
        if (line.trim() !== "") {
          lineEl.append(copyButton(cleanCodeLine(line, block.lang), "Copy line", "line-copy", ""));
        }
        codeEl.append(lineEl);
      });
      return [el("div", { class: "code-block" }, head, el("pre", { class: "code" }, codeEl))];
    }
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
