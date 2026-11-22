import {
  buildCommandCatalog,
  canRunClientCommandInStatus,
  detectComposerTrigger,
  rankCommands,
  resolveClientSlashCommand,
  slashArgOptions,
  type ComposerCommand,
  type ComposerTrigger,
  type SlashArgCommand,
  type SlashArgOption,
} from "../core/composer.js";
import { toolCopyText } from "../core/copy.js";
import { parseMarkdown } from "../core/markdown.js";
import {
  MAX_MENTIONS,
  containsMentionToken,
  formatMentionToken,
  mentionKey,
  mentionLabel,
  normalizeRelativePath,
  removeMentionToken,
  type MentionAttachment,
  type RankedMention,
} from "../core/mentions.js";
import {
  ALLOWED_IMAGE_MIMES,
  MAX_IMAGES,
  MAX_IMAGE_DATA_CHARS,
  type HostMessage,
  type ImageAttachment,
  type ImageMimeType,
  type NoticeAction,
  type PermissionRequestView,
  type StateMessage,
  type ToolCallView,
  type ToolStatus,
  type TranscriptItem,
  type WebviewMessage,
} from "../core/protocol.js";
import { describeContext, folderName, formatCost, formatDuration } from "../core/status-row.js";
import { throttle, type Scheduler, type Throttled } from "../core/throttle.js";
import { copyButton, el, highlightedText, renderBlocks } from "./dom.js";

interface PersistedWebviewState {
  draft: string;
  attachments: MentionAttachment[];
  images?: ImageAttachment[];
  followTail: boolean;
  scrollTop: number;
  openDetails: Record<string, boolean>;
}

declare function acquireVsCodeApi(): {
  postMessage(message: WebviewMessage): void;
  getState?(): unknown;
  setState?(state: PersistedWebviewState): void;
};

const vscode = acquireVsCodeApi();
const send = (message: WebviewMessage) => vscode.postMessage(message);

const STATUS_TEXT: Record<StateMessage["status"], string> = {
  stopped: "Stopped",
  starting: "Starting the agent",
  ready: "Ready",
  busy: "Working",
  cancelling: "Stopping",
  "needs-setup": "Needs setup",
  error: "Error",
  "no-folder": "No folder open",
  untrusted: "Restricted Mode",
  unavailable: "No agent",
};

const root = document.getElementById("root") as HTMLElement;
const header = el("header", { class: "header" });
const banner = el("section", { class: "banner" });
const transcript = el("main", { class: "transcript", dataset: { testid: "transcript" } });
const copyStatus = el("span", { class: "sr-only", role: "status" });
const inputLabel = el("label", { class: "sr-only", for: "chat-input", text: "Message to zycode" });
const input = el("textarea", {
  id: "chat-input",
  class: "input",
  role: "combobox",
  ariaAutocomplete: "list",
  ariaExpanded: "false",
  ariaControls: "composer-menu",
});
input.placeholder = "Ask zycode (@ to mention, / for commands; Enter to send, Shift+Enter for a new line)";
input.rows = 3;
const popover = el("div", {
  id: "composer-menu",
  class: "composer-popover",
  role: "listbox",
  ariaLabel: "Suggestions",
  hidden: true,
});
const chipsBar = el("div", {
  class: "composer-chips",
  role: "list",
  ariaLabel: "Attached context",
  hidden: true,
});
const mentionTriggerButton = el("button", {
  class: "trigger-btn secondary",
  type: "button",
  text: "@",
  ariaLabel: "Mention file, folder, or selection",
  title: "Mention file, folder, or selection (@)",
});
const slashTriggerButton = el("button", {
  class: "trigger-btn secondary",
  type: "button",
  text: "/",
  ariaLabel: "Slash commands",
  title: "Slash commands (/)",
});
const sendButton = el("button", { class: "primary", text: "Send", type: "button" });
const stopButton = el("button", { class: "secondary", text: "Stop", type: "button" });
const jumpBottomButton = el("button", {
  class: "jump-bottom secondary",
  type: "button",
  ariaLabel: "Jump to latest",
  title: "Jump to latest",
  dataset: { label: "↓ Latest" },
});
jumpBottomButton.hidden = true;
const composerBar = el(
  "div",
  { class: "composer-bar" },
  el("div", { class: "composer-triggers" }, mentionTriggerButton, slashTriggerButton),
  el("div", { class: "actions" }, stopButton, sendButton),
);
const composer = el("footer", { class: "composer" }, jumpBottomButton, popover, chipsBar, inputLabel, input, composerBar);
root.append(header, banner, transcript, composer);

/**
 * The text of an answer is drawn at most once in this time. A draw reads the whole text again and makes
 * the whole message again, so it costs more as the answer gets longer, and the model sends a new piece
 * many times in a second. The first piece is drawn at once; the pieces after it join into one draw.
 */
const DRAW_INTERVAL_MS = 80;
const scheduler: Scheduler = {
  now: () => performance.now(),
  setTimeout: (callback, ms) => window.setTimeout(callback, ms),
  clearTimeout: (handle) => window.clearTimeout(handle as number),
};

type MessageEntry = {
  id: string;
  root: HTMLElement;
  text: HTMLElement;
  thoughtDetails: HTMLDetailsElement;
  thought: HTMLElement;
  copyBtn: HTMLButtonElement;
  raw: { text: string; thought: string };
  /** The text that `text` shows now. */
  drawn: string;
  /** The thought text that `thought` shows now. */
  drawnThought: string;
  /** Draws `raw.text` into `text`. */
  draw: Throttled;
  /** Draws `raw.thought` into `thought`. */
  drawThought: Throttled;
  expectedThoughtOpen: boolean;
};

type ToolEntry = {
  id: string;
  root: HTMLElement;
  headerLine: HTMLElement;
  badge: HTMLElement;
  titleEl: HTMLElement;
  copyBtn: HTMLButtonElement;
  outputDetails: HTMLDetailsElement;
  outputSummary: HTMLElement;
  outputBody: HTMLElement;
  outputPre: HTMLPreElement;
  outputCopyBtn: HTMLButtonElement;
  diffWrap: HTMLElement;
  diffBtn: HTMLButtonElement;
  openBtn: HTMLButtonElement;
  openFilePath: string | undefined;
  call: ToolCallView;
  drawnOutput: string;
  drawOutput: Throttled;
  userToggled: boolean;
  expectedOpen: boolean;
};

const MAX_PERSISTED_OPEN_DETAILS = 500;

let state: StateMessage = { type: "state", status: "stopped", canRestart: true };
const messageEls = new Map<string, MessageEntry>();
const toolEntries = new Map<string, ToolEntry>();
const permissionEls = new Map<string, HTMLElement>();
const permissionViews = new Map<string, PermissionRequestView>();
let followTail = true;
let awaitingInitialReset = true;
const openDetails = new Map<string, boolean>();
let pendingScrollRestore: { followTail: boolean; scrollTop: number } | undefined;

type MenuItem =
  | { kind: "slash"; command: ComposerCommand; nameMatches?: number[] }
  | { kind: "slash-arg"; command: SlashArgCommand; option: SlashArgOption; labelMatches?: number[] }
  | { kind: "mention"; item: RankedMention };

const MENTION_LOADING_TIMEOUT_MS = 1000;

let activeTrigger: ComposerTrigger | undefined;
let menuItems: MenuItem[] = [];
let activeIndex = 0;
let menuOpen = false;
let mentionLoading = false;
let dismissedKey: string | undefined;
let mentionSeq = 0;
let mentionTimer: number | undefined;
let mentionLoadingTimer: number | undefined;
let attachments: MentionAttachment[] = [];
let imageAttachments: ImageAttachment[] = [];

const MAX_HISTORY = 50;
const sentHistory: string[] = [];
let historyIndex = -1;
let historyDraft: { text: string; attachments: MentionAttachment[] } | undefined;
let historyEdited = false;

function cloneAttachments(items: readonly MentionAttachment[]): MentionAttachment[] {
  return items.map((item) => (item.range ? { ...item, range: { ...item.range } } : { ...item }));
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sanitizeAttachments(raw: unknown): MentionAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: MentionAttachment[] = [];
  const seen = new Set<string>();
  for (const entry of raw) {
    if (!isPlainRecord(entry)) continue;
    const kind = entry.kind;
    if (kind !== "file" && kind !== "folder" && kind !== "selection") continue;
    if (typeof entry.path !== "string") continue;
    const normalized = normalizeRelativePath(entry.path, kind);
    if (!normalized) continue;
    let attachment: MentionAttachment;
    if (kind === "selection") {
      if (!isPlainRecord(entry.range)) continue;
      const { startLine, endLine } = entry.range;
      if (
        typeof startLine !== "number" ||
        typeof endLine !== "number" ||
        !Number.isSafeInteger(startLine) ||
        !Number.isSafeInteger(endLine) ||
        startLine < 1 ||
        endLine < startLine
      ) {
        continue;
      }
      attachment = { kind, path: normalized, range: { startLine, endLine } };
    } else {
      attachment = { kind, path: normalized };
    }
    const key = mentionKey(attachment);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(attachment);
    if (out.length >= MAX_MENTIONS) break;
  }
  return out;
}

function sanitizeImages(raw: unknown): ImageAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: ImageAttachment[] = [];
  for (const entry of raw) {
    if (!isPlainRecord(entry)) continue;
    if (typeof entry.mimeType !== "string" || !(ALLOWED_IMAGE_MIMES as readonly string[]).includes(entry.mimeType)) {
      continue;
    }
    if (
      typeof entry.data !== "string" ||
      entry.data === "" ||
      entry.data.length > MAX_IMAGE_DATA_CHARS ||
      !/^[A-Za-z0-9+/=]+$/.test(entry.data)
    ) {
      continue;
    }
    const cleanName = typeof entry.name === "string" && entry.name.trim() !== "" ? entry.name.trim().slice(0, 128) : undefined;
    out.push({
      mimeType: entry.mimeType as ImageMimeType,
      data: entry.data,
      ...(cleanName ? { name: cleanName } : {}),
    });
    if (out.length >= MAX_IMAGES) break;
  }
  return out;
}

function loadPersistedState(): PersistedWebviewState | undefined {
  let raw: unknown;
  try {
    raw = vscode.getState?.();
  } catch {
    return undefined;
  }
  if (!isPlainRecord(raw)) return undefined;
  const draft = typeof raw.draft === "string" ? raw.draft.slice(0, 200_000) : "";
  const restoredAttachments = sanitizeAttachments(raw.attachments);
  const restoredImages = sanitizeImages(raw.images);
  const follow = typeof raw.followTail === "boolean" ? raw.followTail : true;
  const scrollTop =
    typeof raw.scrollTop === "number" && Number.isFinite(raw.scrollTop) && raw.scrollTop >= 0 ? raw.scrollTop : 0;
  const details: Record<string, boolean> = {};
  if (isPlainRecord(raw.openDetails)) {
    let count = 0;
    for (const [key, value] of Object.entries(raw.openDetails)) {
      if (typeof value === "boolean") {
        details[key] = value;
        count++;
        if (count >= MAX_PERSISTED_OPEN_DETAILS) break;
      }
    }
  }
  return {
    draft,
    attachments: restoredAttachments,
    images: restoredImages,
    followTail: follow,
    scrollTop,
    openDetails: details,
  };
}

/**
 * `setState` serializes the whole state at each call, and the throttled call runs while the user types.
 * Images beyond this total are not kept, so that a key press costs a few milliseconds at most (4 images of
 * 7,000,000 characters cost 20 to 160 ms).
 */
const IMAGE_STATE_MAX_CHARS = 2_000_000;

function imagesForState(): ImageAttachment[] {
  const kept: ImageAttachment[] = [];
  let total = 0;
  for (const img of imageAttachments) {
    total += img.data.length;
    if (total > IMAGE_STATE_MAX_CHARS) break;
    kept.push({ ...img });
  }
  return kept;
}

function saveWebviewState(): void {
  try {
    vscode.setState?.({
      draft: input.value,
      attachments: cloneAttachments(attachments),
      images: imagesForState(),
      followTail,
      scrollTop: Math.max(0, transcript.scrollTop),
      openDetails: Object.fromEntries(openDetails),
    });
  } catch {
    // Ignore storage failures in constrained webview environments.
  }
}

const saveStateThrottled = throttle(saveWebviewState, 120, scheduler);

function triggerKey(trigger: ComposerTrigger): string {
  return trigger.kind === "slash-arg"
    ? `${trigger.kind}:${trigger.command}:${trigger.range.start}:${trigger.query}`
    : `${trigger.kind}:${trigger.range.start}:${trigger.query}`;
}

function syncJumpBottom(): void {
  jumpBottomButton.hidden = followTail || menuOpen;
}

transcript.addEventListener("scroll", () => {
  followTail = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
  syncJumpBottom();
  if (!pendingScrollRestore) saveStateThrottled.request();
});

function scrollToEnd(): void {
  if (followTail) transcript.scrollTop = transcript.scrollHeight;
}

jumpBottomButton.addEventListener("click", () => {
  followTail = true;
  syncJumpBottom();
  transcript.scrollTop = transcript.scrollHeight;
  saveWebviewState();
});

const copyTimers = new WeakMap<HTMLElement, number>();
let lastCopyAnnouncement = "";

function legacyCopy(text: string): boolean {
  const field = el("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.cssText = "position:fixed;inset-block-start:0;opacity:0";
  document.body.append(field);
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
  }
}

async function writeClipboard(text: string): Promise<boolean> {
  (window as unknown as { __copied?: string }).__copied = text;
  if (text !== "") send({ type: "copy", text });
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return legacyCopy(text);
  }
}

function reportCopy(target: HTMLElement, ok: boolean): void {
  const prev = copyTimers.get(target);
  if (prev !== undefined) window.clearTimeout(prev);
  target.removeAttribute("data-copied");
  target.removeAttribute("data-failed");
  void target.offsetWidth;
  target.setAttribute(ok ? "data-copied" : "data-failed", "");
  if (target.dataset.label !== undefined && (target.dataset.label !== "" || target.classList.contains("line-copy"))) {
    target.dataset.origLabel ??= target.dataset.label;
    target.dataset.label = ok ? "Copied" : "Copy failed";
  }
  let message = ok ? "Copied" : "Copy failed";
  if (message === lastCopyAnnouncement) message += "\u00A0";
  lastCopyAnnouncement = message;
  copyStatus.textContent = "";
  window.requestAnimationFrame(() => {
    copyStatus.textContent = message;
  });
  const timer = window.setTimeout(() => {
    target.removeAttribute("data-copied");
    target.removeAttribute("data-failed");
    if (target.dataset.origLabel !== undefined) {
      target.dataset.label = target.dataset.origLabel;
      delete target.dataset.origLabel;
    }
    copyStatus.textContent = "";
    copyTimers.delete(target);
  }, 2000);
  copyTimers.set(target, timer);
}

function button(label: string, onClick: () => void, className = "secondary"): HTMLButtonElement {
  const b = el("button", { class: className, text: label, type: "button" });
  b.addEventListener("click", onClick);
  return b;
}

/**
 * The clock of the turn (design section 7d). The host says how long the turn has run each time it sends
 * the state; between two messages this page counts with its own clock. Only this one element changes
 * each time, so a menu that is open stays open.
 */
let clockEl: HTMLElement | undefined;
let clockBase: { ms: number; at: number } | undefined;
let clockTimer: number | undefined;

function tickClock(): void {
  if (!clockEl || !clockBase) return;
  const text = formatDuration(clockBase.ms + (performance.now() - clockBase.at));
  if (clockEl.textContent !== text) clockEl.textContent = text;
}

function stopClock(): void {
  clockEl = undefined;
  clockBase = undefined;
  if (clockTimer !== undefined) {
    window.clearInterval(clockTimer);
    clockTimer = undefined;
  }
}

/** The time of the turn: the clock that runs while the agent works, the time it took when it has ended. */
function renderClock(): HTMLElement | null {
  const turn = state.turn;
  if (turn?.elapsedMs !== undefined) {
    clockBase = { ms: turn.elapsedMs, at: performance.now() };
    clockEl = el("span", { class: "clock", title: "Time since you sent the message", text: formatDuration(turn.elapsedMs) });
    clockTimer ??= window.setInterval(tickClock, 500);
    return clockEl;
  }
  stopClock();
  if (turn?.lastMs !== undefined) {
    return el("span", { class: "clock", title: "How long the last turn took", text: `last turn ${formatDuration(turn.lastMs, true)}` });
  }
  return null;
}

/**
 * The context window, as the agent says it (`usage_update`). There is no row when the agent sends none: the
 * page does not count tokens and does not guess a size.
 */
function renderUsage(): HTMLElement | null {
  const usage = state.usage;
  if (!usage) return null;
  const view = describeContext(usage);
  const row = el("div", { class: `usage ${view.level ?? "plain"}`, title: view.title, dataset: { testid: "usage" } }, el("span", { class: "usage-label", text: "Context" }));
  if (usage.size !== undefined) {
    const meter = el("meter", { class: "usage-meter" });
    meter.min = 0;
    meter.max = usage.size;
    meter.value = Math.min(usage.used, usage.size);
    meter.setAttribute("aria-label", "Context used");
    row.append(meter);
  }
  row.append(el("span", { class: "usage-text", text: [view.short, view.count].filter((part) => part !== "").join(" · ") }));
  if (usage.cost) row.append(el("span", { class: "usage-cost", title: "What this chat has cost, as the agent says", text: formatCost(usage.cost) }));
  return row;
}

function renderHeader(): void {
  header.replaceChildren();
  const title = el("div", { class: "title" });
  title.append(el("span", { class: `dot ${state.status}` }), el("strong", { text: "zycode" }), el("span", { class: "status", text: STATUS_TEXT[state.status] }));
  const clock = renderClock();
  if (clock) title.append(clock);
  const alwaysApprove = state.mode?.current === "always-approve";
  root.classList.toggle("always-approve", alwaysApprove);
  if (alwaysApprove) {
    title.append(
      el("span", {
        class: "warn-chip",
        text: "Always approve: no questions",
        title: "Every tool call runs with no question, bash too. Choose Ask or Auto in the mode menu to turn it off.",
      }),
    );
  }
  const facts: string[] = [];
  if (state.agent) facts.push(`agent ${state.agent.version}${state.agent.source === "setting" ? " (your program)" : ""}`);
  if (state.model) facts.push(state.model);
  if (state.cwd) facts.push(`folder ${folderName(state.cwd)}`);
  const meta = el("div", { class: "meta", text: facts.join(" · "), title: state.cwd ?? "" });
  const controls = el("div", { class: "controls" });
  const mode = state.mode;
  if (mode && mode.options.length > 1) {
    const select = el("select", { title: "Permission mode" });
    for (const option of mode.options) {
      const o = el("option", { text: option.name });
      o.value = option.id;
      select.append(o);
    }
    if (!mode.options.some((o) => o.id === mode.current)) {
      const o = el("option", { text: mode.current });
      o.value = mode.current;
      select.append(o);
    }
    select.value = mode.current;
    select.addEventListener("change", () => {
      if (select.value === "ask" || select.value === "auto" || select.value === "always-approve") {
        send({ type: "setMode", value: select.value });
      }
    });
    controls.append(el("label", { class: "mode" }, "Mode ", select));
  } else if (mode) {
    controls.append(el("span", { class: "mode", text: `Mode: ${mode.current}` }));
  } else if (state.status === "ready" || state.status === "busy") {
    controls.append(el("span", { class: "mode", text: "Mode: set by the agent" }));
  }
  const models = state.models;
  if (models && models.options.length > 1) {
    const select = el("select", { title: "Model", ariaLabel: "Model" });
    for (const option of models.options) {
      const o = el("option", { text: option.name });
      o.value = option.id;
      select.append(o);
    }
    if (!models.options.some((o) => o.id === models.current)) {
      const o = el("option", { text: models.current });
      o.value = models.current;
      select.append(o);
    }
    select.value = models.current;
    select.disabled = state.status !== "ready";
    select.addEventListener("change", () => {
      if (select.value !== "") {
        send({ type: "setModel", value: select.value });
      }
    });
    controls.append(el("label", { class: "mode model-picker" }, "Model ", select));
  }
  const thinking = state.thinking;
  if (thinking) {
    const select = el("select", { title: thinking.disabled ?? "Thinking level" });
    select.setAttribute("aria-label", "Thinking level");
    for (const option of thinking.levels) {
      const o = el("option", { text: option.name, title: option.description });
      o.value = option.id;
      select.append(o);
    }
    if (thinking.current === undefined) {
      const o = el("option", { text: "unknown" });
      o.value = "";
      select.append(o);
      select.value = "";
    } else {
      select.value = thinking.current;
    }
    select.disabled = thinking.disabled !== undefined;
    select.addEventListener("change", () => {
      const value = select.value;
      if (value === "off" || value === "minimal" || value === "low" || value === "medium" || value === "high" || value === "xhigh" || value === "max") {
        send({ type: "setThinking", value });
      }
    });
    const label = el("label", { class: "mode thinking", title: thinking.note ?? thinking.disabled ?? "" }, "Thinking ", select);
    controls.append(label);
  }
  controls.append(
    button("New chat", () => send({ type: "newChat" })),
    button("Restart", () => send({ type: "restartAgent" })),
  );
  header.append(title, meta);
  const usageRow = renderUsage();
  if (usageRow) header.append(usageRow);
  header.append(controls, copyStatus);
}

function renderBanner(): void {
  banner.replaceChildren();
  const status = state.status;
  banner.hidden = status === "ready" || status === "busy" || status === "cancelling" || status === "starting" || status === "stopped";
  if (banner.hidden) return;
  banner.className = `banner ${status === "error" ? "error" : "info"}`;
  if (status === "needs-setup") {
    banner.append(
      el("p", { class: "lead", text: state.detail ?? "zycode needs a model and an API key. Run the setup in a terminal." }),
      el(
        "ol",
        { class: "steps" },
        el("li", { text: "Run the setup. A terminal opens and asks for a provider, a model id and an API key." }),
        el("li", {
          text: "Type the key in that terminal only. It is hidden there and saved in the agent's own folder (local.env, readable by your user only). Never type it in this chat or in the VS Code settings.",
        }),
        el("li", { text: "When the terminal closes, zycode restarts the agent. Then say hello." }),
      ),
      el(
        "div",
        { class: "actions left" },
        button("Run setup", () => send({ type: "runSetup" }), "primary"),
        button("Open the guide", () => send({ type: "openGuide" })),
      ),
    );
  } else if (status === "error") {
    banner.append(
      el("pre", { class: "detail", text: state.detail ?? "The agent stopped." }),
      el("div", { class: "actions" }, button("Restart agent", () => send({ type: "restartAgent" }), "primary"), button("Show log", () => send({ type: "openLog" }))),
    );
  } else {
    banner.append(el("p", { text: state.detail ?? STATUS_TEXT[status] }));
    if (status === "unavailable") banner.append(button("Show log", () => send({ type: "openLog" })));
  }
}

function renderComposer(): void {
  const canType = state.status === "ready" || state.status === "busy";
  input.disabled = !canType;
  mentionTriggerButton.disabled = !canType;
  slashTriggerButton.disabled = !canType;
  sendButton.disabled = state.status !== "ready";
  stopButton.hidden = state.status !== "busy" && state.status !== "cancelling";
  stopButton.disabled = state.status === "cancelling";
  if (!canType) closePopover();
}

function applyState(next: StateMessage): void {
  state = next;
  for (const entry of messageEls.values()) {
    entry.thoughtDetails.hidden = entry.raw.thought === "" || state.showThinking === false;
    entry.thoughtDetails.classList.toggle("streaming", state.status === "busy" && entry.raw.text === "" && entry.raw.thought !== "");
  }
  renderHeader();
  renderBanner();
  renderComposer();
  if (menuOpen && (activeTrigger?.kind === "slash" || activeTrigger?.kind === "slash-arg")) {
    refreshOpenMenu();
  }
}

function fill(node: HTMLElement, text: string): void {
  node.replaceChildren(...renderBlocks(parseMarkdown(text)));
}

function addUser(id: string, text: string): void {
  const head = el("div", { class: "msg-head" }, el("div", { class: "who", text: "You" }), copyButton(text, "Copy message", "msg-copy", "Copy"));
  transcript.append(el("article", { class: "msg user" }, head, el("div", { class: "body", text })));
  messageEls.delete(id);
}

function drawText(entry: MessageEntry): void {
  if (entry.drawn === entry.raw.text) return;
  entry.drawn = entry.raw.text;
  fill(entry.text, entry.raw.text);
  // A draw that waited changed the height after the message that asked for it was handled.
  scrollToEnd();
}

function drawThoughtText(entry: MessageEntry): void {
  if (entry.drawnThought === entry.raw.thought) return;
  entry.drawnThought = entry.raw.thought;
  entry.thought.textContent = entry.raw.thought;
  scrollToEnd();
}

function agentMessage(id: string): MessageEntry {
  let entry = messageEls.get(id);
  if (!entry) {
    const thought = el("details", { class: "thought" }, el("summary", { text: "Thinking" }));
    const savedOpen = openDetails.get(`thought:${id}`) === true;
    if (savedOpen) thought.open = true;
    const thoughtBody = el("div", { class: "thought-body" });
    thought.append(thoughtBody);
    thought.hidden = true;
    const text = el("div", { class: "body" });
    const copyBtn = copyButton("", "Copy message", "msg-copy", "Copy");
    copyBtn.hidden = true;
    const head = el("div", { class: "msg-head" }, el("div", { class: "who", text: "zycode" }), copyBtn);
    const rootEl = el("article", { class: "msg agent" }, head, thought, text);
    transcript.append(rootEl);
    const created: MessageEntry = {
      id,
      root: rootEl,
      text,
      thoughtDetails: thought,
      thought: thoughtBody,
      copyBtn,
      raw: { text: "", thought: "" },
      drawn: "",
      drawnThought: "",
      draw: throttle(() => drawText(created), DRAW_INTERVAL_MS, scheduler),
      drawThought: throttle(() => drawThoughtText(created), DRAW_INTERVAL_MS, scheduler),
      expectedThoughtOpen: savedOpen,
    };
    thought.addEventListener("toggle", () => {
      if (thought.open === created.expectedThoughtOpen) return;
      created.expectedThoughtOpen = thought.open;
      openDetails.set(`thought:${id}`, thought.open);
      saveWebviewState();
    });
    entry = created;
    messageEls.set(id, entry);
  }
  return entry;
}

function setAgentText(id: string, text: string, thought: string): void {
  const entry = agentMessage(id);
  entry.raw.text = text;
  entry.raw.thought = thought;
  entry.copyBtn.dataset.copy = text;
  entry.copyBtn.hidden = text === "";
  entry.draw.request();
  entry.thoughtDetails.hidden = thought === "" || state.showThinking === false;
  entry.thoughtDetails.classList.toggle("streaming", state.status === "busy" && text === "" && thought !== "");
  if (text !== "" && entry.drawnThought !== thought) {
    entry.drawThought.cancel();
    drawThoughtText(entry);
  } else {
    entry.drawThought.request();
  }
}

function terminalText(call: ToolCallView): string {
  return call.terminal?.output ?? call.text ?? "";
}

function drawToolOutput(entry: ToolEntry): void {
  const body = terminalText(entry.call);
  if (entry.drawnOutput === body) return;
  entry.drawnOutput = body;
  entry.outputPre.textContent = body;
  entry.outputCopyBtn.dataset.copy = body;
  if (body !== "") {
    if (!entry.outputCopyBtn.isConnected) entry.outputBody.append(entry.outputCopyBtn);
  } else if (entry.outputCopyBtn.isConnected) {
    entry.outputCopyBtn.remove();
  }
  scrollToEnd();
}

function toolFilePath(call: ToolCallView): string | undefined {
  if (!isPlainRecord(call.rawInput) || typeof call.rawInput.path !== "string") return undefined;
  const rawPath = call.rawInput.path;
  const isAbs = rawPath.startsWith("/") || /^[A-Za-z]:[\\/]/.test(rawPath);
  if (isAbs) {
    if (!state.cwd) return undefined;
    const cwdNorm = state.cwd.replace(/\\/g, "/").replace(/\/+/g, "/").replace(/\/+$/, "");
    const pathNorm = rawPath.replace(/\\/g, "/").replace(/\/+/g, "/");
    const prefix = `${cwdNorm}/`;
    const isWinDrive = /^[A-Za-z]:\//.test(prefix);
    const matchesPrefix = isWinDrive
      ? pathNorm.length > prefix.length &&
        pathNorm.slice(0, prefix.length).toLowerCase() === prefix.toLowerCase()
      : pathNorm.startsWith(prefix);
    if (!matchesPrefix) return undefined;
    return normalizeRelativePath(pathNorm.slice(prefix.length), "file");
  }
  return normalizeRelativePath(rawPath, "file");
}

function createToolEntry(call: ToolCallView): ToolEntry {
  const badge = el("span", { class: `badge ${call.status}`, text: call.status.replace("_", " ") });
  const titleEl = el("span", { class: "tool-title", text: call.title });
  const copyBtn = copyButton("", "Copy", "tool-copy", "Copy");
  const headerLine = el("div", { class: "tool-head" }, badge, titleEl);
  const outputSummary = el("summary", { text: "Output" });
  const outputPre = el("pre");
  const outputCopyBtn = copyButton("", "Copy output", "output-copy", "Copy");
  const outputBody = el("div", { class: "tool-output-body" }, outputPre);
  const outputDetails = el("details", { class: "tool-output" }, outputSummary, outputBody);
  const diffBtn = button("Show diff", () => send({ type: "showDiff", toolCallId: call.id }));
  const openBtn = button(
    "Open",
    () => {
      if (entry.openFilePath !== undefined) send({ type: "openFile", path: entry.openFilePath });
    },
    "secondary tool-open-btn",
  );
  const diffWrap = el("div", { class: "actions" });
  const rootEl = el("article", { class: "tool" }, headerLine);

  const savedOpen = openDetails.get(`tool:${call.id}`);
  const expectedOpen = savedOpen ?? false;
  if (savedOpen !== undefined) outputDetails.open = savedOpen;

  const entry: ToolEntry = {
    id: call.id,
    root: rootEl,
    headerLine,
    badge,
    titleEl,
    copyBtn,
    outputDetails,
    outputSummary,
    outputBody,
    outputPre,
    outputCopyBtn,
    diffWrap,
    diffBtn,
    openBtn,
    openFilePath: undefined,
    call,
    drawnOutput: "",
    drawOutput: throttle(() => drawToolOutput(entry), DRAW_INTERVAL_MS, scheduler),
    userToggled: savedOpen !== undefined,
    expectedOpen,
  };

  outputDetails.addEventListener("toggle", () => {
    if (outputDetails.open === entry.expectedOpen) return;
    entry.expectedOpen = outputDetails.open;
    entry.userToggled = true;
    openDetails.set(`tool:${entry.id}`, outputDetails.open);
    saveWebviewState();
  });

  return entry;
}

function syncToolOutputState(entry: ToolEntry, body: string, hasOutput: boolean): void {
  if (hasOutput) {
    const summaryText =
      entry.call.terminal?.exitCode !== undefined ? `Output (exit code ${entry.call.terminal.exitCode})` : "Output";
    if (entry.outputSummary.textContent !== summaryText) entry.outputSummary.textContent = summaryText;
    if (!entry.outputDetails.isConnected) {
      if (entry.diffWrap.isConnected) entry.root.insertBefore(entry.outputDetails, entry.diffWrap);
      else entry.root.append(entry.outputDetails);
    }
    if (!entry.userToggled) {
      const nextOpen = entry.call.status === "failed" || body.length < 400;
      if (entry.outputDetails.open !== nextOpen) {
        entry.expectedOpen = nextOpen;
        entry.outputDetails.open = nextOpen;
      }
    }
  } else if (entry.outputDetails.isConnected) {
    entry.outputDetails.remove();
  }
}

function upsertTool(call: ToolCallView): void {
  let entry = toolEntries.get(call.id);
  if (!entry) {
    entry = createToolEntry(call);
    toolEntries.set(call.id, entry);
    transcript.append(entry.root);
  } else {
    entry.call = call;
  }
  entry.root.dataset.status = call.status;
  entry.root.dataset.kind = call.kind;
  entry.badge.className = `badge ${call.status}`;
  entry.badge.textContent = call.status.replace("_", " ");
  entry.titleEl.textContent = call.title;

  const copyInfo = toolCopyText(call);
  if (copyInfo.text !== "") {
    entry.copyBtn.dataset.copy = copyInfo.text;
    entry.copyBtn.setAttribute("aria-label", copyInfo.label);
    entry.copyBtn.title = copyInfo.label;
    if (!entry.copyBtn.isConnected) entry.headerLine.append(entry.copyBtn);
  } else if (entry.copyBtn.isConnected) {
    entry.copyBtn.remove();
  }

  const body = terminalText(call);
  const hasOutput = body !== "" || call.terminal?.exitCode !== undefined;
  syncToolOutputState(entry, body, hasOutput);

  const filePath = toolFilePath(call);
  entry.openFilePath = filePath;
  if (filePath !== undefined) {
    entry.openBtn.title = `Open ${filePath}`;
    entry.openBtn.setAttribute("aria-label", `Open ${filePath}`);
    if (!entry.openBtn.isConnected) {
      if (entry.diffBtn.isConnected) entry.diffWrap.insertBefore(entry.openBtn, entry.diffBtn);
      else entry.diffWrap.append(entry.openBtn);
    }
  } else if (entry.openBtn.isConnected) {
    entry.openBtn.remove();
  }
  if (call.canDiff) {
    if (!entry.diffBtn.isConnected) entry.diffWrap.append(entry.diffBtn);
  } else if (entry.diffBtn.isConnected) {
    entry.diffBtn.remove();
  }
  if (call.canDiff || filePath !== undefined) {
    if (!entry.diffWrap.isConnected) entry.root.append(entry.diffWrap);
  } else if (entry.diffWrap.isConnected) {
    entry.diffWrap.remove();
  }

  if (call.status === "completed" || call.status === "failed") {
    entry.drawOutput.cancel();
    drawToolOutput(entry);
  } else {
    entry.drawOutput.request();
  }
}

function appendToolOutput(id: string, delta: string, status: ToolStatus): void {
  const entry = toolEntries.get(id);
  if (!entry) return;
  const nextOutput = (entry.call.terminal?.output ?? entry.call.text ?? "") + delta;
  const nextTerminal = entry.call.terminal
    ? { ...entry.call.terminal, output: nextOutput }
    : { output: nextOutput };
  const nextCall: ToolCallView = {
    ...entry.call,
    status,
    terminal: nextTerminal,
  };
  delete nextCall.text;
  entry.call = nextCall;
  if (entry.root.dataset.status !== status) {
    entry.root.dataset.status = status;
    entry.badge.className = `badge ${status}`;
    entry.badge.textContent = status.replace("_", " ");
  }
  syncToolOutputState(entry, nextOutput, true);
  entry.drawOutput.request();
}

function describeAnswer(done: { outcome: string; optionId?: string }, request: PermissionRequestView): string {
  if (done.outcome !== "selected") return "Cancelled";
  const option = request.options.find((o) => o.id === done.optionId);
  return option ? option.name : (done.optionId ?? "Answered");
}

function renderPermission(request: PermissionRequestView, done?: { outcome: string; optionId?: string }): void {
  permissionViews.set(request.requestId, request);
  const card = permissionEls.get(request.requestId) ?? el("article", { class: "permission" });
  if (!permissionEls.has(request.requestId)) {
    permissionEls.set(request.requestId, card);
    transcript.append(card);
  }
  card.className = `permission ${request.kind}`;
  const parts: Node[] = [el("div", { class: "perm-title", text: request.kind === "trust" ? `Project trust: ${request.title}` : `Allow: ${request.title}` })];
  if (request.kind === "trust") parts.push(el("p", { class: "perm-note", text: request.summary }));
  else if (request.summary !== "") {
    parts.push(
      el("pre", {
        class: "perm-summary",
        text: request.summary,
        title: "Click to copy",
        dataset: { copy: request.summary, label: "Click to copy" },
      }),
    );
  }
  if (request.canDiff && request.toolCallId !== undefined) {
    const toolCallId = request.toolCallId;
    parts.push(el("div", { class: "actions left" }, button("Show diff", () => send({ type: "showDiff", toolCallId }))));
  } else if (request.diffNote) {
    parts.push(el("div", { class: "perm-note", text: request.diffNote }));
  }
  if (done) {
    parts.push(el("div", { class: "perm-done", text: describeAnswer(done, request) }));
  } else {
    const actions = el("div", { class: "actions" });
    for (const option of request.options) {
      const className = option.kind.startsWith("allow") ? "primary" : "secondary";
      actions.append(button(option.name, () => send({ type: "permissionAnswer", requestId: request.requestId, optionId: option.id }), className));
    }
    parts.push(actions);
  }
  card.replaceChildren(...parts);
}

const NOTICE_MESSAGE: Record<NoticeAction["id"], WebviewMessage> = {
  runSetup: { type: "runSetup" },
  openGuide: { type: "openGuide" },
  openAgentConfig: { type: "openAgentConfig" },
  openLog: { type: "openLog" },
  saveAll: { type: "saveAll" },
};

function addNotice(level: string, text: string, actions?: NoticeAction[]): void {
  const node = el("div", { class: `notice ${level}` }, el("span", { text }));
  if (actions && actions.length > 0) {
    node.append(
      el(
        "div",
        { class: "actions left" },
        ...actions.map((action) => button(action.label, () => send(NOTICE_MESSAGE[action.id]))),
      ),
    );
  }
  transcript.append(node);
}

function renderItem(item: TranscriptItem): void {
  switch (item.type) {
    case "user":
      addUser(item.id, item.text);
      return;
    case "agent":
      setAgentText(item.id, item.text, item.thought);
      return;
    case "tool":
      upsertTool(item.toolCall);
      return;
    case "permission":
      renderPermission(item.request, item.done);
      return;
    case "notice":
      addNotice(item.level, item.text, item.actions);
      return;
  }
}

function resetTranscript(items: TranscriptItem[]): void {
  const isInitial = awaitingInitialReset;
  awaitingInitialReset = false;
  transcript.replaceChildren();
  for (const entry of messageEls.values()) {
    entry.draw.cancel();
    entry.drawThought.cancel();
  }
  for (const tool of toolEntries.values()) tool.drawOutput.cancel();
  messageEls.clear();
  toolEntries.clear();
  permissionEls.clear();
  permissionViews.clear();
  closePopover();
  if (!isInitial) {
    attachments = [];
    imageAttachments = [];
    renderChips();
    openDetails.clear();
    pendingScrollRestore = undefined;
    followTail = true;
  } else {
    const validDetailKeys = new Set<string>();
    for (const item of items) {
      if (item.type === "agent") validDetailKeys.add(`thought:${item.id}`);
      else if (item.type === "tool") validDetailKeys.add(`tool:${item.toolCall.id}`);
    }
    for (const key of [...openDetails.keys()]) {
      if (!validDetailKeys.has(key)) openDetails.delete(key);
    }
  }
  for (const item of items) renderItem(item);
  if (isInitial && pendingScrollRestore) {
    const restore = pendingScrollRestore;
    pendingScrollRestore = undefined;
    if (!restore.followTail && items.length > 0) {
      followTail = false;
      transcript.scrollTop = restore.scrollTop;
    } else {
      followTail = true;
    }
    syncJumpBottom();
    if (followTail) scrollToEnd();
  } else {
    followTail = true;
    syncJumpBottom();
    scrollToEnd();
  }
  saveWebviewState();
}

function onHostMessage(message: HostMessage): void {
  switch (message.type) {
    case "state":
      applyState(message);
      return;
    case "reset":
      resetTranscript(message.transcript);
      return;
    case "userMessage":
      addUser(message.id, message.text);
      break;
    case "agentText":
    case "agentThought": {
      const entry = agentMessage(message.messageId);
      const text = message.type === "agentText" ? entry.raw.text + message.text : entry.raw.text;
      const thought = message.type === "agentThought" ? entry.raw.thought + message.text : entry.raw.thought;
      setAgentText(message.messageId, text, thought);
      break;
    }
    case "toolCall":
      upsertTool(message.toolCall);
      break;
    case "toolCallOutput":
      appendToolOutput(message.id, message.delta, message.status);
      break;
    case "permission":
      renderPermission(message);
      break;
    case "permissionDone": {
      const request = permissionViews.get(message.requestId);
      if (request) {
        renderPermission(request, message.optionId === undefined ? { outcome: message.outcome } : { outcome: message.outcome, optionId: message.optionId });
      }
      break;
    }
    case "notice":
      addNotice(message.level, message.text, message.actions);
      break;
    case "turnEnd":
      for (const entry of messageEls.values()) {
        entry.draw.cancel();
        drawText(entry);
        entry.drawThought.cancel();
        drawThoughtText(entry);
      }
      for (const tool of toolEntries.values()) {
        tool.drawOutput.cancel();
        drawToolOutput(tool);
      }
      return;
    case "diffState":
      return;
    case "mentionResults":
      if (message.requestId !== mentionSeq || activeTrigger?.kind !== "mention") return;
      clearMentionLoadingTimer();
      mentionLoading = false;
      menuItems = message.items.map((item) => ({ kind: "mention", item }));
      activeIndex = 0;
      renderPopover();
      return;
    case "insertMention": {
      const item = message.item;
      const token = formatMentionToken(item);
      const cursor = input.selectionStart ?? input.value.length;
      const before = input.value.slice(0, cursor);
      const after = input.value.slice(cursor);
      const leadSpace = before.length > 0 && !/\s$/.test(before) ? " " : "";
      const trailSpace = after.startsWith(" ") ? "" : " ";
      const inserted = `${leadSpace}${token}${trailSpace}`;
      input.value = before + inserted + after;
      const nextPos = before.length + inserted.length;
      input.setSelectionRange(nextPos, nextPos);
      addAttachment(item);
      saveWebviewState();
      input.focus();
      return;
    }
  }
  scrollToEnd();
}

function clearMentionTimer(): void {
  if (mentionTimer !== undefined) {
    window.clearTimeout(mentionTimer);
    mentionTimer = undefined;
  }
}

function clearMentionLoadingTimer(): void {
  if (mentionLoadingTimer !== undefined) {
    window.clearTimeout(mentionLoadingTimer);
    mentionLoadingTimer = undefined;
  }
}

function startMentionLoading(): void {
  mentionLoading = true;
  clearMentionLoadingTimer();
  mentionLoadingTimer = window.setTimeout(() => {
    mentionLoadingTimer = undefined;
    if (mentionLoading) {
      mentionLoading = false;
      renderPopover();
    }
  }, MENTION_LOADING_TIMEOUT_MS);
}

function syncComboboxAria(): void {
  input.setAttribute("aria-expanded", menuOpen ? "true" : "false");
  if (menuOpen && menuItems.length > 0 && activeIndex >= 0 && activeIndex < menuItems.length) {
    input.setAttribute("aria-activedescendant", `composer-opt-${activeIndex}`);
  } else {
    input.removeAttribute("aria-activedescendant");
  }
}

function closePopover(): void {
  clearMentionTimer();
  clearMentionLoadingTimer();
  mentionLoading = false;
  activeTrigger = undefined;
  menuItems = [];
  activeIndex = 0;
  menuOpen = false;
  popover.hidden = true;
  popover.replaceChildren();
  syncComboboxAria();
  syncJumpBottom();
}

function syncActiveOption(): void {
  const options = popover.querySelectorAll<HTMLElement>("[role='option']");
  options.forEach((node, index) => {
    const isActive = index === activeIndex;
    node.classList.toggle("active", isActive);
    node.setAttribute("aria-selected", isActive ? "true" : "false");
    if (isActive) {
      node.scrollIntoView?.({ block: "nearest" });
    }
  });
  syncComboboxAria();
}

function renderPopover(): void {
  if (!activeTrigger) {
    closePopover();
    return;
  }
  menuOpen = true;
  popover.hidden = false;
  syncJumpBottom();

  const headerText =
    activeTrigger.kind === "slash"
      ? "Commands"
      : activeTrigger.kind === "slash-arg"
        ? `/${activeTrigger.command} options`
        : "Context · files, folders, selection";
  const nodes: Node[] = [el("div", { class: "popover-head", text: headerText })];

  if (menuItems.length === 0) {
    const emptyText =
      activeTrigger.kind === "mention"
        ? mentionLoading
          ? "Searching workspace…"
          : "No matching files or folders"
        : activeTrigger.kind === "slash-arg"
          ? `No matching options for /${activeTrigger.command}`
          : "No matching commands";
    nodes.push(el("div", { class: "popover-empty", role: "status", text: emptyText }));
    popover.replaceChildren(...nodes);
    syncComboboxAria();
    return;
  }

  if (activeIndex >= menuItems.length) activeIndex = menuItems.length - 1;
  if (activeIndex < 0) activeIndex = 0;

  menuItems.forEach((entry, index) => {
    const active = index === activeIndex;
    const row = el("div", {
      id: `composer-opt-${index}`,
      class: active ? "popover-item active" : "popover-item",
      role: "option",
      ariaSelected: active ? "true" : "false",
      dataset: { index: String(index) },
    });

    if (entry.kind === "slash") {
      const top = el("div", { class: "popover-main" });
      const title = el("span", { class: "popover-title" }, document.createTextNode("/"), ...highlightedText(entry.command.name, entry.nameMatches));
      top.append(title);
      if (entry.command.argHint) {
        top.append(el("span", { class: "popover-hint", text: entry.command.argHint }));
      }
      top.append(el("span", { class: `popover-badge ${entry.command.source}`, text: entry.command.source === "agent" ? "agent" : "zycode" }));
      row.append(top);
      if (entry.command.description !== "") {
        row.append(el("div", { class: "popover-detail", text: entry.command.description }));
      }
    } else if (entry.kind === "slash-arg") {
      const top = el("div", { class: "popover-main" });
      top.append(el("span", { class: "popover-title" }, ...highlightedText(entry.option.label, entry.labelMatches)));
      if (entry.option.current) {
        top.append(el("span", { class: "popover-badge current", text: "current" }));
      }
      row.append(top);
      if (entry.option.description) {
        row.append(el("div", { class: "popover-detail", text: entry.option.description }));
      }
    } else {
      const item = entry.item;
      const top = el("div", { class: "popover-main" });
      top.append(
        el("span", { class: `mention-icon ${item.kind}`, ariaHidden: true }),
        el("span", { class: "popover-title" }, ...highlightedText(item.label, item.labelMatches)),
      );
      if (item.badge) {
        top.append(
          el("span", {
            class: `popover-badge ${item.badge}`,
            text: item.badge === "secret" ? "may hold secrets" : item.badge,
          }),
        );
      }
      row.append(top);
      if (item.detail !== "") {
        row.append(el("div", { class: "popover-detail" }, ...highlightedText(item.detail, item.detailMatches)));
      }
    }
    nodes.push(row);
  });

  popover.replaceChildren(...nodes);
  syncComboboxAria();
}

function renderChips(): void {
  chipsBar.replaceChildren();
  chipsBar.hidden = attachments.length === 0 && imageAttachments.length === 0;
  for (const att of attachments) {
    const label = mentionLabel(att);
    const title =
      att.kind === "selection" && att.range
        ? `${att.path} (lines ${att.range.startLine}-${att.range.endLine})`
        : att.path;
    const removeBtn = el("button", {
      class: "chip-remove",
      type: "button",
      text: "×",
      ariaLabel: `Remove ${label}`,
      title: `Remove ${label}`,
    });
    removeBtn.addEventListener("click", () => removeAttachment(att));
    const canOpen = att.kind === "file" || att.kind === "selection";
    const labelEl = el("span", {
      class: canOpen ? "chip-label clickable" : "chip-label",
      text: label,
      ...(canOpen ? { role: "button", title: `Open ${title}` } : {}),
    });
    if (canOpen) {
      labelEl.tabIndex = 0;
      const open = () =>
        send({
          type: "openFile",
          path: att.path,
          ...(att.range ? { line: att.range.startLine } : {}),
        });
      labelEl.addEventListener("click", open);
      labelEl.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      });
    }
    chipsBar.append(
      el(
        "div",
        { class: "chip", role: "listitem", title },
        el("span", { class: `mention-icon ${att.kind}`, ariaHidden: true }),
        labelEl,
        removeBtn,
      ),
    );
  }
  imageAttachments.forEach((img, index) => {
    const label = `[Image: ${img.name ?? img.mimeType}]`;
    const removeBtn = el("button", {
      class: "chip-remove",
      type: "button",
      text: "×",
      ariaLabel: `Remove ${label}`,
      title: `Remove ${label}`,
    });
    removeBtn.addEventListener("click", () => {
      imageAttachments.splice(index, 1);
      renderChips();
      saveWebviewState();
      input.focus();
    });
    chipsBar.append(
      el(
        "div",
        { class: "chip image-chip", role: "listitem", title: label },
        el("span", { class: "chip-label", text: label }),
        removeBtn,
      ),
    );
  });
}

function addImageAttachment(mimeType: string, base64Data: string, name?: string): void {
  if (input.disabled) return;
  if (state.supportsImages === false) {
    addNotice("warning", "This agent does not support image attachments.");
    scrollToEnd();
    return;
  }
  if (!(ALLOWED_IMAGE_MIMES as readonly string[]).includes(mimeType)) return;
  if (imageAttachments.length >= MAX_IMAGES) {
    addNotice("warning", `At most ${MAX_IMAGES} images can be attached to a message.`);
    scrollToEnd();
    return;
  }
  if (base64Data === "" || base64Data.length > MAX_IMAGE_DATA_CHARS || !/^[A-Za-z0-9+/=]+$/.test(base64Data)) {
    addNotice("warning", "Image is empty or too large (limit 5 MB).");
    scrollToEnd();
    return;
  }
  const cleanName = name && name.trim() !== "" ? name.trim().slice(0, 128) : undefined;
  imageAttachments.push({
    mimeType: mimeType as ImageMimeType,
    data: base64Data,
    ...(cleanName ? { name: cleanName } : {}),
  });
  renderChips();
  saveWebviewState();
  if (imagesForState().length < imageAttachments.length) {
    addNotice(
      "info",
      "Large images are not kept when this view reloads. Send the message first, or attach the image again after a reload.",
    );
    scrollToEnd();
  }
}

function readImageFile(file: File): void {
  if (!(ALLOWED_IMAGE_MIMES as readonly string[]).includes(file.type)) return;
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    if (typeof reader.result !== "string") return;
    const comma = reader.result.indexOf(",");
    if (comma < 0) return;
    addImageAttachment(file.type, reader.result.slice(comma + 1), file.name);
  });
  reader.readAsDataURL(file);
}

function addAttachment(attachment: MentionAttachment): void {
  const key = mentionKey(attachment);
  if (attachments.some((item) => mentionKey(item) === key)) return;
  if (attachments.length >= MAX_MENTIONS) return;
  attachments.push(attachment);
  renderChips();
  saveWebviewState();
}

function removeAttachment(target: MentionAttachment): void {
  const key = mentionKey(target);
  attachments = attachments.filter((item) => mentionKey(item) !== key);
  input.value = removeMentionToken(input.value, target);
  renderChips();
  saveWebviewState();
  updateTrigger();
  input.focus();
}

function syncAttachmentsWithInput(): void {
  if (attachments.length === 0) return;
  const next = attachments.filter((item) => containsMentionToken(input.value, item));
  if (next.length !== attachments.length) {
    attachments = next;
    renderChips();
  }
}

function dispatchClientCommand(text: string): "dispatched-new" | "dispatched" | "notice" | "none" {
  const resolved = resolveClientSlashCommand(text, state);
  if (!resolved) return "none";
  if (resolved.kind === "message") {
    if (!canRunClientCommandInStatus(resolved.message, state.status)) {
      addNotice("info", "Wait until the current action finishes before running this command.");
      scrollToEnd();
      return "notice";
    }
    send(resolved.message);
    return resolved.message.type === "newChat" ? "dispatched-new" : "dispatched";
  }
  addNotice(resolved.level, resolved.text);
  scrollToEnd();
  return "notice";
}

function requestMentions(query: string): void {
  clearMentionTimer();
  const requestId = ++mentionSeq;
  startMentionLoading();
  send({ type: "queryMentions", requestId, query });
}

function refreshOpenMenu(): void {
  if (!activeTrigger) return;
  if (activeTrigger.kind === "slash") {
    const ranked = rankCommands(buildCommandCatalog(state), activeTrigger.query);
    menuItems = ranked.map((entry) => ({
      kind: "slash",
      command: entry.command,
      ...(entry.nameMatches ? { nameMatches: entry.nameMatches } : {}),
    }));
    renderPopover();
    return;
  }
  if (activeTrigger.kind === "slash-arg") {
    const command = activeTrigger.command;
    const opts = slashArgOptions(command, state, activeTrigger.query);
    menuItems = opts.map((entry) => ({
      kind: "slash-arg",
      command,
      option: entry.option,
      ...(entry.labelMatches ? { labelMatches: entry.labelMatches } : {}),
    }));
    renderPopover();
  }
}

function updateTrigger(force = false): void {
  if (input.disabled) {
    closePopover();
    return;
  }
  const cursor = input.selectionStart ?? input.value.length;
  const trigger = detectComposerTrigger(input.value, cursor);
  if (!trigger) {
    dismissedKey = undefined;
    closePopover();
    return;
  }
  const key = triggerKey(trigger);
  if (!force && dismissedKey === key) {
    return;
  }
  if (dismissedKey !== undefined && dismissedKey !== key) {
    dismissedKey = undefined;
  }

  const prev = activeTrigger;
  const sameKindAndStart = prev !== undefined && prev.kind === trigger.kind && prev.range.start === trigger.range.start;
  const wasMentionOpen = menuOpen && prev?.kind === "mention";
  activeTrigger = trigger;
  if (!sameKindAndStart) {
    activeIndex = 0;
  }

  if (trigger.kind === "slash" || trigger.kind === "slash-arg") {
    clearMentionTimer();
    clearMentionLoadingTimer();
    mentionLoading = false;
    refreshOpenMenu();
    return;
  }

  if (wasMentionOpen && prev.kind === "mention" && prev.query === trigger.query && !force) {
    return;
  }

  menuItems = [];
  activeIndex = 0;

  if (!wasMentionOpen || trigger.query === "" || force) {
    requestMentions(trigger.query);
    renderPopover();
  } else {
    clearMentionTimer();
    startMentionLoading();
    renderPopover();
    const query = trigger.query;
    mentionTimer = window.setTimeout(() => {
      if (activeTrigger?.kind === "mention" && activeTrigger.query === query) {
        requestMentions(query);
      }
    }, 40);
  }
}

function replaceTriggerRange(trigger: ComposerTrigger, inserted: string): void {
  const val = input.value;
  const prefix = val.slice(0, trigger.range.start);
  const suffix = val.slice(trigger.range.end);
  const cleanedSuffix = inserted.endsWith(" ") && suffix.startsWith(" ") ? suffix.slice(1) : suffix;
  input.value = prefix + inserted + cleanedSuffix;
  const nextCursor = prefix.length + inserted.length;
  input.setSelectionRange(nextCursor, nextCursor);
}

function acceptMenuItem(index: number, mode: "enter" | "tab"): void {
  if (!activeTrigger || index < 0 || index >= menuItems.length) return;
  const selected = menuItems[index];
  if (!selected) return;

  const trigger = activeTrigger;
  const val = input.value;
  const prefix = val.slice(0, trigger.range.start);
  const suffix = val.slice(trigger.range.end);

  if (selected.kind === "slash") {
    const cmd = selected.command;
    const openedViaButtonOnDraft = trigger.range.start === 0 && trigger.range.end === 0 && suffix.trim() !== "";
    if (
      !cmd.takesArgs &&
      mode === "enter" &&
      cmd.source === "client" &&
      ((prefix.trim() === "" && suffix.trim() === "") || openedViaButtonOnDraft)
    ) {
      const resolved = resolveClientSlashCommand(`/${cmd.name}`, state);
      if (resolved?.kind === "message" && canRunClientCommandInStatus(resolved.message, state.status)) {
        closePopover();
        if (!openedViaButtonOnDraft) {
          input.value = "";
          attachments = [];
          if (cmd.name === "new") imageAttachments = [];
          renderChips();
        }
        saveWebviewState();
        dispatchClientCommand(`/${cmd.name}`);
      } else {
        if (!openedViaButtonOnDraft) {
          replaceTriggerRange(trigger, `/${cmd.name}`);
        }
        closePopover();
        saveWebviewState();
        if (openedViaButtonOnDraft) {
          dispatchClientCommand(`/${cmd.name}`);
        }
      }
      return;
    }
    replaceTriggerRange(trigger, `/${cmd.name} `);
    closePopover();
    saveWebviewState();
    if (cmd.takesArgs) updateTrigger(true);
    return;
  }

  if (selected.kind === "slash-arg") {
    const argId = selected.option.id;
    if (mode === "enter" && state.status === "ready" && suffix.trim() === "") {
      const fullText = prefix + argId;
      closePopover();
      input.value = "";
      attachments = [];
      renderChips();
      saveWebviewState();
      dispatchClientCommand(fullText);
      return;
    }
    replaceTriggerRange(trigger, mode === "tab" ? `${argId} ` : argId);
    closePopover();
    saveWebviewState();
    return;
  }

  const item = selected.item;
  if (item.kind === "folder" && mode === "tab") {
    replaceTriggerRange(trigger, `@${item.path}`);
    dismissedKey = undefined;
    saveWebviewState();
    updateTrigger(true);
    return;
  }

  const attachment: MentionAttachment =
    item.kind === "selection" && item.range
      ? { kind: "selection", path: item.path, range: item.range }
      : { kind: item.kind, path: item.path };
  const token = formatMentionToken(attachment);
  replaceTriggerRange(trigger, `${token} `);
  addAttachment(attachment);
  closePopover();
}

function recordHistory(text: string): void {
  historyIndex = -1;
  historyEdited = false;
  historyDraft = undefined;
  if (text.trim() === "") return;
  if (sentHistory.at(-1) !== text) {
    sentHistory.push(text);
    if (sentHistory.length > MAX_HISTORY) sentHistory.shift();
  }
}

function restoreHistoryDraft(): void {
  input.value = historyDraft?.text ?? "";
  attachments = historyDraft ? cloneAttachments(historyDraft.attachments) : [];
  historyIndex = -1;
  historyEdited = false;
  historyDraft = undefined;
  input.setSelectionRange(input.value.length, input.value.length);
  renderChips();
  saveStateThrottled.request();
}

function submit(): void {
  syncAttachmentsWithInput();
  const text = input.value;
  if (text.trim() === "" && imageAttachments.length === 0) return;
  if (text.trim() !== "") {
    const outcome = dispatchClientCommand(text);
    if (outcome !== "none") {
      closePopover();
      if (outcome === "dispatched" || outcome === "dispatched-new") {
        recordHistory(text);
        input.value = "";
        attachments = [];
        if (outcome === "dispatched-new") {
          imageAttachments = [];
        }
        renderChips();
        saveWebviewState();
      }
      return;
    }
  }
  if (state.status !== "ready") return;
  if (imageAttachments.length > 0 && state.supportsImages === false) {
    addNotice("warning", "This agent does not support image attachments.");
    scrollToEnd();
    return;
  }
  closePopover();
  followTail = true;
  syncJumpBottom();
  recordHistory(text);
  const payload: WebviewMessage = {
    type: "send",
    text,
    ...(attachments.length > 0 ? { mentions: [...attachments] } : {}),
    ...(imageAttachments.length > 0 ? { images: [...imageAttachments] } : {}),
  };
  send(payload);
  input.value = "";
  attachments = [];
  imageAttachments = [];
  renderChips();
  saveWebviewState();
}

sendButton.addEventListener("click", submit);
stopButton.addEventListener("click", () => send({ type: "cancel" }));

input.addEventListener("paste", (event) => {
  if (input.disabled) return;
  const items = event.clipboardData?.items;
  if (!items) return;
  let handledImage = false;
  for (const item of Array.from(items)) {
    if ((ALLOWED_IMAGE_MIMES as readonly string[]).includes(item.type)) {
      const file = item.getAsFile();
      if (file) {
        if (state.supportsImages === false) {
          event.preventDefault();
          addNotice("warning", "This agent does not support image attachments.");
          scrollToEnd();
          return;
        }
        handledImage = true;
        readImageFile(file);
      }
    }
  }
  if (handledImage) event.preventDefault();
});

composer.addEventListener("dragover", (event) => {
  if (input.disabled) return;
  if (event.dataTransfer?.types.includes("Files")) {
    event.preventDefault();
  }
});

composer.addEventListener("drop", (event) => {
  if (input.disabled) return;
  const files = event.dataTransfer?.files;
  if (!files || files.length === 0) return;
  let hasImage = false;
  for (const file of Array.from(files)) {
    if ((ALLOWED_IMAGE_MIMES as readonly string[]).includes(file.type)) {
      if (state.supportsImages === false) {
        event.preventDefault();
        addNotice("warning", "This agent does not support image attachments.");
        scrollToEnd();
        return;
      }
      hasImage = true;
      readImageFile(file);
    }
  }
  if (hasImage) event.preventDefault();
});

mentionTriggerButton.addEventListener("click", () => {
  if (input.disabled) return;
  input.focus();
  const cursor = input.selectionStart ?? input.value.length;
  const current = detectComposerTrigger(input.value, cursor);
  if (current?.kind === "mention") {
    dismissedKey = undefined;
    updateTrigger(true);
    return;
  }
  const before = input.value.slice(0, cursor);
  const after = input.value.slice(cursor);
  const needSpace = before.length > 0 && !/[\s(]$/.test(before);
  const insert = needSpace ? " @" : "@";
  input.value = before + insert + after;
  const nextPos = before.length + insert.length;
  input.setSelectionRange(nextPos, nextPos);
  dismissedKey = undefined;
  saveStateThrottled.request();
  updateTrigger(true);
});

slashTriggerButton.addEventListener("click", () => {
  if (input.disabled) return;
  input.focus();
  const cursor = input.selectionStart ?? input.value.length;
  const current = detectComposerTrigger(input.value, cursor);
  if (current && (current.kind === "slash" || current.kind === "slash-arg")) {
    dismissedKey = undefined;
    updateTrigger(true);
    return;
  }
  if (input.value.trim() === "") {
    input.value = "/";
    input.setSelectionRange(1, 1);
  } else if (!/^[ \t]*\//.test(input.value)) {
    activeTrigger = { kind: "slash", query: "", range: { start: 0, end: 0 } };
    activeIndex = 0;
    dismissedKey = undefined;
    clearMentionTimer();
    clearMentionLoadingTimer();
    mentionLoading = false;
    refreshOpenMenu();
    return;
  } else {
    const firstSpace = input.value.search(/\s/);
    const pos = firstSpace >= 0 ? firstSpace : input.value.length;
    input.setSelectionRange(pos, pos);
  }
  dismissedKey = undefined;
  saveStateThrottled.request();
  updateTrigger(true);
});

popover.addEventListener("mousedown", (event) => {
  event.preventDefault();
});

popover.addEventListener("mouseover", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const option = target.closest<HTMLElement>("[role='option'][data-index]");
  if (!option) return;
  const idx = Number(option.dataset.index);
  if (Number.isInteger(idx) && idx >= 0 && idx < menuItems.length && idx !== activeIndex) {
    activeIndex = idx;
    syncActiveOption();
  }
});

popover.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const option = target.closest<HTMLElement>("[role='option'][data-index]");
  if (!option) return;
  const idx = Number(option.dataset.index);
  if (Number.isInteger(idx)) {
    acceptMenuItem(idx, "enter");
    input.focus();
  }
});

document.addEventListener("mousedown", (event) => {
  if (!menuOpen) return;
  const target = event.target;
  if (target instanceof Node && composer.contains(target)) return;
  closePopover();
});

input.addEventListener("input", () => {
  if (historyIndex >= 0) {
    historyEdited = true;
  }
  syncAttachmentsWithInput();
  saveStateThrottled.request();
  updateTrigger();
});
input.addEventListener("click", () => updateTrigger());
input.addEventListener("keyup", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "Home" || event.key === "End") {
    updateTrigger();
  }
});

input.addEventListener("keydown", (event) => {
  const composing = event.isComposing || event.keyCode === 229;
  if (composing) {
    if (
      event.key === "Enter" ||
      event.key === "Tab" ||
      event.key === "ArrowUp" ||
      event.key === "ArrowDown" ||
      event.key === "Escape"
    ) {
      return;
    }
  }

  if (menuOpen) {
    if (event.key === "Escape") {
      event.preventDefault();
      dismissedKey = activeTrigger ? triggerKey(activeTrigger) : undefined;
      closePopover();
      return;
    }
    if (activeTrigger?.kind === "mention" && mentionLoading) {
      if ((event.key === "Tab" && !event.shiftKey) || (event.key === "Enter" && !event.shiftKey)) {
        event.preventDefault();
        return;
      }
    }
    if (menuItems.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        activeIndex = (activeIndex + 1) % menuItems.length;
        syncActiveOption();
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        activeIndex = (activeIndex - 1 + menuItems.length) % menuItems.length;
        syncActiveOption();
        return;
      }
      if (event.key === "Tab" && !event.shiftKey) {
        event.preventDefault();
        acceptMenuItem(activeIndex, "tab");
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        acceptMenuItem(activeIndex, "enter");
        return;
      }
    }
  }

  if (event.key === "Escape" && state.status === "busy") {
    event.preventDefault();
    send({ type: "cancel" });
    return;
  }

  if (event.key === "Escape" && !menuOpen && state.status !== "busy" && historyIndex >= 0 && historyDraft !== undefined) {
    event.preventDefault();
    restoreHistoryDraft();
    return;
  }

  const noModifiers = !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey;
  if (event.key === "ArrowUp" && !menuOpen && noModifiers && sentHistory.length > 0) {
    const cursor = input.selectionStart ?? 0;
    const selectionEnd = input.selectionEnd ?? cursor;
    const onFirstLine = !input.value.slice(0, cursor).includes("\n");
    if (historyIndex === -1) {
      if (input.value === "" || (cursor === 0 && selectionEnd === 0)) {
        event.preventDefault();
        historyDraft = { text: input.value, attachments: cloneAttachments(attachments) };
        historyIndex = sentHistory.length - 1;
        historyEdited = false;
        input.value = sentHistory[historyIndex]!;
        input.setSelectionRange(input.value.length, input.value.length);
        syncAttachmentsWithInput();
        saveStateThrottled.request();
        return;
      }
    } else if (!historyEdited && onFirstLine) {
      event.preventDefault();
      if (historyIndex > 0) {
        historyIndex--;
      }
      input.value = sentHistory[historyIndex]!;
      input.setSelectionRange(input.value.length, input.value.length);
      syncAttachmentsWithInput();
      saveStateThrottled.request();
      return;
    }
  }

  if (event.key === "ArrowDown" && !menuOpen && noModifiers && historyIndex >= 0) {
    const cursor = input.selectionEnd ?? input.value.length;
    const onLastLine = !input.value.slice(cursor).includes("\n");
    if (onLastLine) {
      event.preventDefault();
      if (!historyEdited && historyIndex < sentHistory.length - 1) {
        historyIndex++;
        input.value = sentHistory[historyIndex]!;
        input.setSelectionRange(input.value.length, input.value.length);
        syncAttachmentsWithInput();
        saveStateThrottled.request();
      } else {
        restoreHistoryDraft();
      }
      return;
    }
  }

  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});
transcript.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const link = target.closest("a[data-url]");
  if (link instanceof HTMLElement && link.dataset.url) {
    event.preventDefault();
    send({ type: "openLink", url: link.dataset.url });
    return;
  }
  const copyTarget = target.closest<HTMLElement>("[data-copy]");
  if (copyTarget && copyTarget.dataset.copy !== undefined) {
    if (
      (copyTarget.classList.contains("inline-code") || copyTarget.classList.contains("perm-summary")) &&
      (window.getSelection()?.toString() ?? "") !== ""
    ) {
      return;
    }
    event.preventDefault();
    void writeClipboard(copyTarget.dataset.copy).then((ok) => reportCopy(copyTarget, ok));
  }
});

window.addEventListener("message", (event: MessageEvent<HostMessage>) => onHostMessage(event.data));
const restored = loadPersistedState();
if (restored) {
  input.value = restored.draft;
  attachments = restored.attachments;
  imageAttachments = restored.images ?? [];
  renderChips();
  for (const [key, value] of Object.entries(restored.openDetails)) {
    openDetails.set(key, value);
  }
  pendingScrollRestore = { followTail: restored.followTail, scrollTop: restored.scrollTop };
}
applyState(state);
send({ type: "ready" });
