import {
  buildCommandCatalog,
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
  formatMentionToken,
  mentionKey,
  mentionLabel,
  removeMentionToken,
  type MentionAttachment,
  type RankedMention,
} from "../core/mentions.js";
import type {
  HostMessage,
  NoticeAction,
  PermissionRequestView,
  StateMessage,
  ToolCallView,
  TranscriptItem,
  WebviewMessage,
} from "../core/protocol.js";
import { describeContext, folderName, formatCost, formatDuration } from "../core/status-row.js";
import { throttle, type Scheduler, type Throttled } from "../core/throttle.js";
import { copyButton, el, highlightedText, renderBlocks } from "./dom.js";

declare function acquireVsCodeApi(): { postMessage(message: WebviewMessage): void };

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
  root: HTMLElement;
  text: HTMLElement;
  thought: HTMLElement;
  copyBtn: HTMLButtonElement;
  raw: { text: string; thought: string };
  /** The text that `text` shows now. */
  drawn: string;
  /** Draws `raw.text` into `text`. */
  draw: Throttled;
};

let state: StateMessage = { type: "state", status: "stopped", canRestart: true };
const messageEls = new Map<string, MessageEntry>();
const toolEls = new Map<string, HTMLElement>();
const permissionEls = new Map<string, HTMLElement>();
const permissionViews = new Map<string, PermissionRequestView>();
let followTail = true;

type MenuItem =
  | { kind: "slash"; command: ComposerCommand; nameMatches?: number[] }
  | { kind: "slash-arg"; command: SlashArgCommand; option: SlashArgOption; labelMatches?: number[] }
  | { kind: "mention"; item: RankedMention };

let activeTrigger: ComposerTrigger | undefined;
let menuItems: MenuItem[] = [];
let activeIndex = 0;
let menuOpen = false;
let mentionLoading = false;
let dismissedKey: string | undefined;
let mentionSeq = 0;
let mentionTimer: number | undefined;
let attachments: MentionAttachment[] = [];

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
});

function scrollToEnd(): void {
  if (followTail) transcript.scrollTop = transcript.scrollHeight;
}

jumpBottomButton.addEventListener("click", () => {
  followTail = true;
  syncJumpBottom();
  transcript.scrollTop = transcript.scrollHeight;
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
    const details = entry.thought.parentElement as HTMLElement;
    details.hidden = entry.raw.thought === "" || state.showThinking === false;
    details.classList.toggle("streaming", state.status === "busy" && entry.raw.text === "" && entry.raw.thought !== "");
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

function agentMessage(id: string): MessageEntry {
  let entry = messageEls.get(id);
  if (!entry) {
    const thought = el("details", { class: "thought" }, el("summary", { text: "Thinking" }));
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
      root: rootEl,
      text,
      thought: thoughtBody,
      copyBtn,
      raw: { text: "", thought: "" },
      drawn: "",
      draw: throttle(() => drawText(created), DRAW_INTERVAL_MS, scheduler),
    };
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
  const details = entry.thought.parentElement as HTMLElement;
  details.hidden = thought === "" || state.showThinking === false;
  details.classList.toggle("streaming", state.status === "busy" && text === "" && thought !== "");
  entry.thought.textContent = thought;
}

function terminalText(call: ToolCallView): string {
  return call.terminal?.output ?? call.text ?? "";
}

function upsertTool(call: ToolCallView): void {
  const copyInfo = toolCopyText(call);
  const headerLine = el(
    "div",
    { class: "tool-head" },
    el("span", { class: `badge ${call.status}`, text: call.status.replace("_", " ") }),
    el("span", { class: "tool-title", text: call.title }),
    copyInfo.text !== "" ? copyButton(copyInfo.text, copyInfo.label, "tool-copy", "Copy") : null,
  );
  const body = terminalText(call);
  const parts: (Node | null)[] = [headerLine];
  if (body !== "" || call.terminal?.exitCode !== undefined) {
    const outputBody = el("div", { class: "tool-output-body" }, el("pre", { text: body }));
    if (body !== "") outputBody.append(copyButton(body, "Copy output", "output-copy", "Copy"));
    const output = el(
      "details",
      { class: "tool-output" },
      el("summary", { text: call.terminal?.exitCode !== undefined ? `Output (exit code ${call.terminal.exitCode})` : "Output" }),
      outputBody,
    );
    if (call.status === "failed" || body.length < 400) output.setAttribute("open", "");
    parts.push(output);
  }
  if (call.canDiff) {
    parts.push(el("div", { class: "actions" }, button("Show diff", () => send({ type: "showDiff", toolCallId: call.id }))));
  }
  let node = toolEls.get(call.id);
  if (!node) {
    node = el("article", { class: "tool" });
    toolEls.set(call.id, node);
    transcript.append(node);
  }
  node.dataset.status = call.status;
  node.dataset.kind = call.kind;
  node.replaceChildren(...parts.filter((p): p is Node => p !== null));
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
  transcript.replaceChildren();
  for (const entry of messageEls.values()) entry.draw.cancel();
  messageEls.clear();
  toolEls.clear();
  permissionEls.clear();
  permissionViews.clear();
  attachments = [];
  renderChips();
  closePopover();
  followTail = true;
  syncJumpBottom();
  for (const item of items) renderItem(item);
  scrollToEnd();
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
    case "diffState":
      return;
    case "mentionResults":
      if (message.requestId !== mentionSeq || activeTrigger?.kind !== "mention") return;
      mentionLoading = false;
      menuItems = message.items.map((item) => ({ kind: "mention", item }));
      activeIndex = 0;
      renderPopover();
      return;
  }
  scrollToEnd();
}

function clearMentionTimer(): void {
  if (mentionTimer !== undefined) {
    window.clearTimeout(mentionTimer);
    mentionTimer = undefined;
  }
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
        top.append(el("span", { class: `popover-badge ${item.badge}`, text: item.badge }));
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
  chipsBar.hidden = attachments.length === 0;
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
    chipsBar.append(
      el(
        "div",
        { class: "chip", role: "listitem", title },
        el("span", { class: `mention-icon ${att.kind}`, ariaHidden: true }),
        el("span", { class: "chip-label", text: label }),
        removeBtn,
      ),
    );
  }
}

function addAttachment(attachment: MentionAttachment): void {
  const key = mentionKey(attachment);
  if (attachments.some((item) => mentionKey(item) === key)) return;
  if (attachments.length >= MAX_MENTIONS) return;
  attachments.push(attachment);
  renderChips();
}

function removeAttachment(target: MentionAttachment): void {
  const key = mentionKey(target);
  attachments = attachments.filter((item) => mentionKey(item) !== key);
  input.value = removeMentionToken(input.value, target);
  renderChips();
  updateTrigger();
  input.focus();
}

function dispatchClientCommand(text: string): "dispatched" | "notice" | "none" {
  const resolved = resolveClientSlashCommand(text, state);
  if (!resolved) return "none";
  if (resolved.kind === "message") {
    send(resolved.message);
    return "dispatched";
  }
  addNotice(resolved.level, resolved.text);
  scrollToEnd();
  return "notice";
}

function requestMentions(query: string): void {
  clearMentionTimer();
  const requestId = ++mentionSeq;
  mentionLoading = true;
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
    mentionLoading = true;
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
    if (!cmd.takesArgs && mode === "enter" && prefix.trim() === "" && suffix.trim() === "" && cmd.source === "client") {
      if (state.status === "ready") {
        closePopover();
        input.value = "";
        dispatchClientCommand(`/${cmd.name}`);
      } else {
        replaceTriggerRange(trigger, `/${cmd.name}`);
        closePopover();
      }
      return;
    }
    replaceTriggerRange(trigger, `/${cmd.name} `);
    closePopover();
    if (cmd.takesArgs) updateTrigger(true);
    return;
  }

  if (selected.kind === "slash-arg") {
    const argId = selected.option.id;
    if (mode === "enter" && state.status === "ready" && suffix.trim() === "") {
      const fullText = prefix + argId;
      closePopover();
      input.value = "";
      dispatchClientCommand(fullText);
      return;
    }
    replaceTriggerRange(trigger, mode === "tab" ? `${argId} ` : argId);
    closePopover();
    return;
  }

  const item = selected.item;
  if (item.kind === "folder" && mode === "tab") {
    replaceTriggerRange(trigger, `@${item.path}`);
    dismissedKey = undefined;
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

function submit(): void {
  const text = input.value;
  if (text.trim() === "" || state.status !== "ready") return;
  if (attachments.length === 0) {
    const outcome = dispatchClientCommand(text);
    if (outcome !== "none") {
      closePopover();
      if (outcome === "dispatched") input.value = "";
      return;
    }
  }
  closePopover();
  followTail = true;
  syncJumpBottom();
  const payload: WebviewMessage =
    attachments.length > 0 ? { type: "send", text, mentions: [...attachments] } : { type: "send", text };
  send(payload);
  input.value = "";
  attachments = [];
  renderChips();
}

sendButton.addEventListener("click", submit);
stopButton.addEventListener("click", () => send({ type: "cancel" }));

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
    mentionLoading = false;
    refreshOpenMenu();
    return;
  } else {
    const firstSpace = input.value.search(/\s/);
    const pos = firstSpace >= 0 ? firstSpace : input.value.length;
    input.setSelectionRange(pos, pos);
  }
  dismissedKey = undefined;
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

input.addEventListener("input", () => updateTrigger());
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
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
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
      if (event.key === "Tab") {
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
applyState(state);
send({ type: "ready" });
