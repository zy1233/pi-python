import { parseMarkdown } from "../core/markdown.js";
import type {
  HostMessage,
  NoticeAction,
  PermissionRequestView,
  StateMessage,
  ToolCallView,
  TranscriptItem,
  WebviewMessage,
} from "../core/protocol.js";
import { el, renderBlocks } from "./dom.js";

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
const input = el("textarea", { class: "input" });
input.placeholder = "Ask zycode (Enter to send, Shift+Enter for a new line)";
input.rows = 3;
const sendButton = el("button", { class: "primary", text: "Send", type: "button" });
const stopButton = el("button", { class: "secondary", text: "Stop", type: "button" });
const composer = el("footer", { class: "composer" }, input, el("div", { class: "actions" }, stopButton, sendButton));
root.append(header, banner, transcript, composer);

let state: StateMessage = { type: "state", status: "stopped", canRestart: true };
const messageEls = new Map<string, { root: HTMLElement; text: HTMLElement; thought: HTMLElement; raw: { text: string; thought: string } }>();
const toolEls = new Map<string, HTMLElement>();
const permissionEls = new Map<string, HTMLElement>();
const permissionViews = new Map<string, PermissionRequestView>();
let followTail = true;

transcript.addEventListener("scroll", () => {
  followTail = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
});

function scrollToEnd(): void {
  if (followTail) transcript.scrollTop = transcript.scrollHeight;
}

function button(label: string, onClick: () => void, className = "secondary"): HTMLButtonElement {
  const b = el("button", { class: className, text: label, type: "button" });
  b.addEventListener("click", onClick);
  return b;
}

function renderHeader(): void {
  header.replaceChildren();
  const title = el("div", { class: "title" });
  title.append(el("span", { class: `dot ${state.status}` }), el("strong", { text: "zycode" }), el("span", { class: "status", text: STATUS_TEXT[state.status] }));
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
  const meta = el("div", { class: "meta", text: facts.join(" · ") });
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
  controls.append(
    button("New chat", () => send({ type: "newChat" })),
    button("Restart", () => send({ type: "restartAgent" })),
  );
  header.append(title, meta, controls);
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
  sendButton.disabled = state.status !== "ready";
  stopButton.hidden = state.status !== "busy" && state.status !== "cancelling";
  stopButton.disabled = state.status === "cancelling";
}

function applyState(next: StateMessage): void {
  state = next;
  for (const entry of messageEls.values()) {
    (entry.thought.parentElement as HTMLElement).hidden = entry.raw.thought === "" || state.showThinking === false;
  }
  renderHeader();
  renderBanner();
  renderComposer();
}

function fill(node: HTMLElement, text: string): void {
  node.replaceChildren(...renderBlocks(parseMarkdown(text)));
}

function addUser(id: string, text: string): void {
  transcript.append(el("article", { class: "msg user" }, el("div", { class: "who", text: "You" }), el("div", { class: "body", text })));
  messageEls.delete(id);
}

function agentMessage(id: string) {
  let entry = messageEls.get(id);
  if (!entry) {
    const thought = el("details", { class: "thought" }, el("summary", { text: "Thinking" }));
    const thoughtBody = el("div", { class: "thought-body" });
    thought.append(thoughtBody);
    thought.hidden = true;
    const text = el("div", { class: "body" });
    const rootEl = el("article", { class: "msg agent" }, el("div", { class: "who", text: "zycode" }), thought, text);
    transcript.append(rootEl);
    entry = { root: rootEl, text, thought: thoughtBody, raw: { text: "", thought: "" } };
    messageEls.set(id, entry);
  }
  return entry;
}

function setAgentText(id: string, text: string, thought: string): void {
  const entry = agentMessage(id);
  entry.raw.text = text;
  entry.raw.thought = thought;
  fill(entry.text, text);
  const details = entry.thought.parentElement as HTMLElement;
  details.hidden = thought === "" || state.showThinking === false;
  entry.thought.textContent = thought;
}

function terminalText(call: ToolCallView): string {
  return call.terminal?.output ?? call.text ?? "";
}

function upsertTool(call: ToolCallView): void {
  const headerLine = el("div", { class: "tool-head" }, el("span", { class: `badge ${call.status}`, text: call.status.replace("_", " ") }), el("span", { class: "tool-title", text: call.title }));
  const body = terminalText(call);
  const parts: (Node | null)[] = [headerLine];
  if (body !== "" || call.terminal?.exitCode !== undefined) {
    const output = el("details", { class: "tool-output" }, el("summary", { text: call.terminal?.exitCode !== undefined ? `Output (exit code ${call.terminal.exitCode})` : "Output" }), el("pre", { text: body }));
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
  else if (request.summary !== "") parts.push(el("pre", { class: "perm-summary", text: request.summary }));
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
  messageEls.clear();
  toolEls.clear();
  permissionEls.clear();
  permissionViews.clear();
  followTail = true;
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
  }
  scrollToEnd();
}

function submit(): void {
  const text = input.value;
  if (text.trim() === "" || state.status !== "ready") return;
  send({ type: "send", text });
  input.value = "";
}

sendButton.addEventListener("click", submit);
stopButton.addEventListener("click", () => send({ type: "cancel" }));
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
});
transcript.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const link = target.closest("a[data-url]");
  if (link instanceof HTMLElement && link.dataset.url) {
    event.preventDefault();
    send({ type: "openLink", url: link.dataset.url });
  }
});

window.addEventListener("message", (event: MessageEvent<HostMessage>) => onHostMessage(event.data));
applyState(state);
send({ type: "ready" });
