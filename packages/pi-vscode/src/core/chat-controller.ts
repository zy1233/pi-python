import { statSync } from "node:fs";

import { AcpConnection, AuthRequiredError } from "./acp-connection.js";
import type {
  InitializeResponse,
  NewSessionResponse,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionConfigOption,
  SessionNotification,
} from "./acp-connection.js";
import { AgentProcess, type StopTimings } from "./agent-process.js";
import type { Host } from "./host.js";
import {
  prepareBundledBinary,
  resolveAgentCommand,
  type AgentCommand,
} from "./locate.js";
import type {
  HostMessage,
  ModeState,
  NoticeLevel,
  PermissionModeName,
  PermissionOptionView,
  PermissionOutcome,
  PermissionRequestView,
  StateMessage,
  Status,
  TranscriptItem,
} from "./protocol.js";
import { ToolCallStore, summarizeInput } from "./tool-calls.js";
import { checkAgentIdentity } from "./version.js";

export const TRUST_NOTE =
  "This is the zycode agent's own question about this project. It is not the VS Code Workspace Trust " +
  "question, which you already answered. It decides if zycode may load the extensions, saved " +
  "workflows, prompt files and skills that this project ships.";

const TRUST_ID_PREFIX = "trust-";
const TRUST_REJECT_ID = "dont-trust-project";
const TRUST_ACCEPT_ID = "trust-project";
const SETUP_FALLBACK_ARGS = ["--setup"];

export interface ControllerOptions {
  stopTimings?: StopTimings;
  /** Time for `initialize` (the cold start of the one-file binary is about 2 s). */
  startTimeoutMs?: number;
  /** Time to wait for the turn to end after `session/cancel`. */
  cancelTimeoutMs?: number;
}

interface PendingPermission {
  resolve: (response: RequestPermissionResponse) => void;
  optionIds: Set<string>;
  view: PermissionRequestView;
}

interface TerminalAuthMethod {
  id: string;
  args: string[];
  env: Record<string, string>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The state of the chat and the agent behind it (docs/specs/2026-11-04-vscode-extension-design.md,
 * sections 3 to 5). It has no `vscode` import: the view subscribes with `onMessage` and sends
 * intents by calling its methods.
 */
export class ChatController {
  private status: Status = "stopped";
  private detail: string | undefined;
  private proc: AgentProcess | undefined;
  private conn: AcpConnection | undefined;
  private command: AgentCommand | undefined;
  private initResult: InitializeResponse | undefined;
  private sessionId: string | undefined;
  private cwd: string | undefined;
  private mode: ModeState | undefined;
  private model: string | undefined;
  private opening = false;
  private earlyUpdates: SessionNotification[] = [];
  private generation = 0;
  private stopping = false;
  private startPromise: Promise<void> | undefined;
  private cancelTimer: NodeJS.Timeout | undefined;
  private counter = 0;
  private segmentId: string | undefined;
  private modeNoticeShown = false;

  private items: TranscriptItem[] = [];
  private readonly tools = new ToolCallStore();
  private readonly pending = new Map<string, PendingPermission>();
  private readonly listeners = new Set<(message: HostMessage) => void>();
  private readonly ignoredKinds = new Set<string>();

  constructor(
    private readonly host: Host,
    private readonly options: ControllerOptions = {},
  ) {}

  // ----- subscription and snapshots

  onMessage(listener: (message: HostMessage) => void): { dispose(): void } {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  }

  stateMessage(): StateMessage {
    const message: StateMessage = {
      type: "state",
      status: this.status,
      canRestart: this.status !== "starting",
    };
    if (this.detail !== undefined) message.detail = this.detail;
    if (this.initResult && this.command) {
      message.agent = {
        version: this.initResult.agentInfo?.version ?? "unknown",
        source: this.command.source,
      };
    }
    if (this.cwd !== undefined) message.cwd = this.cwd;
    if (this.mode) message.mode = this.mode;
    if (this.model !== undefined) message.model = this.model;
    return message;
  }

  transcript(): TranscriptItem[] {
    return this.items.map((item) => structuredClone(item));
  }

  get currentStatus(): Status {
    return this.status;
  }

  // ----- lifecycle

  /** Starts the agent and opens a session. Does nothing if the agent is running. */
  start(): Promise<void> {
    this.startPromise ??= this.doStart().finally(() => {
      this.startPromise = undefined;
    });
    return this.startPromise;
  }

  private async doStart(): Promise<void> {
    if (this.proc && !this.proc.hasExited) {
      if (this.status === "needs-setup" || (this.status === "ready" && !this.sessionId)) {
        await this.openSession();
      }
      return;
    }
    this.stopping = false;
    if (!this.host.isTrusted()) {
      this.setStatus("untrusted", "Workspace Trust is off for this folder. zycode does not start in Restricted Mode.");
      return;
    }
    const folder = this.host.sessionFolder();
    if (folder === undefined) {
      this.setStatus("no-folder", "Open a folder to chat with zycode.");
      return;
    }
    this.cwd = folder;
    const settings = this.host.settings();
    const resolved = resolveAgentCommand({
      agentPath: settings.agentPath,
      agentArgs: settings.agentArgs,
      extensionPath: this.host.extensionPath,
      platform: this.host.platform,
      exists: (file) => fileExistsSync(file),
    });
    if (!resolved.ok) {
      this.setStatus("unavailable", resolved.reason);
      return;
    }
    this.command = resolved.command;
    this.setStatus("starting");
    const generation = ++this.generation;
    try {
      if (resolved.command.source === "bundled") {
        const problem = await prepareBundledBinary(resolved.command.command, this.host.platform);
        if (problem) this.host.log.warn(problem);
      }
      const proc = await AgentProcess.start(resolved.command, { cwd: folder, log: this.host.log });
      if (generation !== this.generation) {
        void proc.stop(this.options.stopTimings);
        return;
      }
      this.proc = proc;
      void proc.exited.then((info) => this.onProcessExit(generation, proc, info));
      const conn = AcpConnection.open(proc, {
        onUpdate: (notification) => this.onUpdate(notification),
        requestPermission: (request, signal) => this.onPermissionRequest(request, signal),
      });
      this.conn = conn;
      const init = await this.withTimeout(conn.initialize(this.host.extensionVersion), proc);
      if (generation !== this.generation) return;
      this.initResult = init;
      const verdict = checkAgentIdentity({
        protocolVersion: init.protocolVersion,
        agentName: init.agentInfo?.name,
        agentVersion: init.agentInfo?.version,
        source: resolved.command.source,
        expectedVersion: this.host.agentVersionPin,
      });
      if (verdict.kind === "fail") {
        this.setError(verdict.text);
        await this.stopProcess();
        return;
      }
      if (verdict.kind === "warn") {
        this.host.log.warn(verdict.text);
        this.host.notify("warning", verdict.text);
        this.pushNotice("warning", verdict.text);
      } else if (verdict.log) {
        this.host.log.info(verdict.log);
      }
      await this.openSession();
    } catch (error) {
      if (generation !== this.generation) return;
      if (this.status !== "error") this.setError(await this.describeStartFailure(error));
      await this.stopProcess();
    }
  }

  private async withTimeout<T>(work: Promise<T>, proc: AgentProcess): Promise<T> {
    const timeoutMs = this.options.startTimeoutMs ?? 60_000;
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error(`The agent did not answer initialize in ${timeoutMs / 1000} s.`)), timeoutMs);
    });
    const exited = proc.exited.then((info) => {
      throw new Error(this.exitText(info, proc));
    });
    try {
      return await Promise.race([work, timeout, exited]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private async describeStartFailure(error: unknown): Promise<string> {
    // A dying agent closes the connection a moment before the exit event: wait briefly for it, so the
    // message has the exit code and the stderr tail and not only "ACP connection closed".
    const proc = this.proc;
    if (proc) {
      const info = await Promise.race([proc.exited, new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), 1000))]);
      if (info) return this.exitText(info, proc);
    }
    const code = (error as NodeJS.ErrnoException | undefined)?.code;
    const where = this.command?.command ?? "the agent";
    if (code === "ENOENT") return `The agent program was not found: ${where}`;
    if (code === "EACCES") return `The agent program cannot be run (permission denied): ${where}`;
    if (code === "ENOEXEC") return `The agent program has the wrong format for this platform: ${where}`;
    return messageOf(error);
  }

  private async openSession(): Promise<void> {
    const conn = this.conn;
    const cwd = this.cwd;
    if (!conn || cwd === undefined) return;
    const generation = this.generation;
    this.opening = true;
    this.earlyUpdates = [];
    let response: NewSessionResponse;
    try {
      response = await conn.newSession(cwd);
    } catch (error) {
      this.opening = false;
      if (generation !== this.generation) return;
      if (error instanceof AuthRequiredError) {
        this.setStatus(
          "needs-setup",
          "zycode needs a model and an API key. Run the setup in a terminal.",
        );
        return;
      }
      this.setError(`Could not open a session: ${messageOf(error)}`);
      return;
    }
    if (generation !== this.generation) return;
    this.sessionId = response.sessionId;
    this.readConfigOptions(response.configOptions ?? [], response.modes?.currentModeId);
    const held = this.earlyUpdates;
    this.earlyUpdates = [];
    this.opening = false;
    for (const notification of held) this.onUpdate(notification);
    await this.applyPermissionSetting();
    if (generation !== this.generation) return;
    this.setStatus("ready");
  }

  private readConfigOptions(options: SessionConfigOption[], modesCurrent?: string): void {
    this.mode = undefined;
    for (const option of options) {
      if (option.type !== "select") continue;
      const flat = flattenSelect(option);
      if (option.id === "mode") {
        this.mode = { current: String(option.currentValue), options: flat };
      } else if (option.id === "model") {
        const current = flat.find((choice) => choice.id === String(option.currentValue));
        this.model = current?.name ?? String(option.currentValue);
      }
    }
    if (!this.mode && modesCurrent !== undefined) {
      // The agent reports a mode but no option to change it: show it, offer no choice.
      this.mode = { current: modesCurrent, options: [] };
    }
  }

  private async applyPermissionSetting(): Promise<void> {
    const wanted = this.host.settings().permissionMode;
    await this.setSessionMode(wanted, false);
  }

  /** Sets the permission mode of the open session. Used for the setting and for the view's switch. */
  async applyPermissionMode(mode: PermissionModeName): Promise<void> {
    await this.setSessionMode(mode, true);
    this.emit(this.stateMessage());
  }

  private async setSessionMode(wanted: PermissionModeName, announce: boolean): Promise<void> {
    const conn = this.conn;
    const sessionId = this.sessionId;
    if (!conn || !sessionId) return;
    const mode = this.mode;
    const canChoose = mode !== undefined && mode.options.some((option) => option.id === wanted);
    if (!canChoose) {
      if (wanted === "auto" && !this.modeNoticeShown) {
        this.modeNoticeShown = true;
        this.pushNotice("warning", "This agent version has no Auto mode.");
      }
      return;
    }
    if (mode.current === wanted) return;
    try {
      await conn.setConfigOption(sessionId, "mode", wanted);
      this.mode = { ...mode, current: wanted };
      if (announce) this.pushNotice("info", `Permission mode: ${wanted}.`);
    } catch (error) {
      this.pushNotice("warning", `Could not set the permission mode: ${messageOf(error)}`);
    }
  }

  /** Stops the turn, the process and the connection. */
  async stop(): Promise<void> {
    this.generation++;
    this.stopping = true;
    this.clearCancelTimer();
    this.cancelAllPermissions();
    const conn = this.conn;
    const sessionId = this.sessionId;
    if (conn && sessionId && (this.status === "busy" || this.status === "cancelling")) {
      await conn.cancel(sessionId).catch(() => undefined);
    }
    await this.stopProcess();
    this.sessionId = undefined;
    this.opening = false;
    this.setStatus("stopped");
  }

  private async stopProcess(): Promise<void> {
    const proc = this.proc;
    const conn = this.conn;
    this.proc = undefined;
    this.conn = undefined;
    if (proc) await proc.stop(this.options.stopTimings);
    conn?.close();
  }

  /** Stops the agent and starts it again. The chat starts empty unless `keepTranscript` is set. */
  async restart(keepTranscript = false): Promise<void> {
    await this.stop();
    if (!keepTranscript) this.resetTranscript();
    await this.start();
  }

  async dispose(): Promise<void> {
    await this.stop();
    this.listeners.clear();
  }

  private onProcessExit(generation: number, proc: AgentProcess, info: { code: number | null; signal: NodeJS.Signals | null }): void {
    if (generation !== this.generation || this.stopping || proc !== this.proc) return;
    this.cancelAllPermissions();
    this.clearCancelTimer();
    const text = this.exitText(info, proc);
    this.host.log.error(text);
    this.proc = undefined;
    this.conn?.close();
    this.conn = undefined;
    this.sessionId = undefined;
    this.opening = false;
    this.pushNotice("error", text);
    this.setError(text);
  }

  private exitText(info: { code: number | null; signal: NodeJS.Signals | null }, proc: AgentProcess | undefined): string {
    const how = info.signal ? `signal ${info.signal}` : `exit code ${info.code}`;
    const tail = proc?.stderrTail(2000).split("\n").slice(-12).join("\n").trim();
    return tail ? `The agent exited (${how}).\n${tail}` : `The agent exited (${how}).`;
  }

  // ----- chat

  /** Sends a prompt. Returns `false` if it was not sent (the view should not offer it). */
  async send(text: string): Promise<boolean> {
    const conn = this.conn;
    const sessionId = this.sessionId;
    if (this.status !== "ready" || !conn || !sessionId || text.trim() === "") return false;
    const dirty = this.cwd !== undefined ? this.host.dirtyFiles(this.cwd) : [];
    if (dirty.length > 0) {
      this.pushNotice(
        "info",
        `${dirty.length} file${dirty.length === 1 ? " has" : "s have"} unsaved changes. The agent reads the files on disk.`,
      );
    }
    this.segmentId = undefined;
    const id = this.nextId("user");
    this.items.push({ type: "user", id, text });
    this.emit({ type: "userMessage", id, text });
    this.setStatus("busy");
    const generation = this.generation;
    try {
      const stopReason = await conn.prompt(sessionId, text);
      if (generation !== this.generation) return true;
      this.finishTurn(stopReason);
    } catch (error) {
      if (generation !== this.generation) return true;
      if ((this.status as Status) !== "error") {
        this.pushNotice("error", `The turn failed: ${messageOf(error)}`);
        this.finishTurn("error");
      }
    }
    return true;
  }

  private finishTurn(stopReason: string): void {
    this.segmentId = undefined;
    this.clearCancelTimer();
    this.cancelAllPermissions();
    if (stopReason !== "end_turn" && stopReason !== "cancelled" && stopReason !== "error") {
      this.pushNotice("info", `The turn ended: ${stopReason}.`);
    }
    this.emit({ type: "turnEnd", stopReason });
    if (this.status === "busy" || this.status === "cancelling") this.setStatus("ready");
  }

  /** Stops the turn: `session/cancel`, and every open question is answered as cancelled. */
  async cancel(): Promise<void> {
    const conn = this.conn;
    const sessionId = this.sessionId;
    if (this.status !== "busy" || !conn || !sessionId) return;
    this.setStatus("cancelling");
    this.cancelAllPermissions();
    this.clearCancelTimer();
    this.cancelTimer = setTimeout(() => {
      if (this.status === "cancelling") {
        this.pushNotice("warning", "The agent did not stop. Restart it?");
      }
    }, this.options.cancelTimeoutMs ?? 10_000);
    try {
      await conn.cancel(sessionId);
    } catch (error) {
      this.host.log.warn(`session/cancel failed: ${messageOf(error)}`);
    }
  }

  /** A new chat: closes the session, opens another one. Also the retry after a login error. */
  async newChat(): Promise<void> {
    if (!this.conn || this.status === "busy" || this.status === "cancelling" || this.status === "starting") return;
    const conn = this.conn;
    const previous = this.sessionId;
    this.sessionId = undefined;
    this.resetTranscript();
    if (previous && this.initResult?.agentCapabilities?.sessionCapabilities?.close !== undefined) {
      await conn.closeSession(previous).catch((error) => {
        this.host.log.warn(`session/close failed: ${messageOf(error)}`);
      });
    }
    await this.openSession();
  }

  private resetTranscript(): void {
    this.items = [];
    this.tools.clear();
    this.segmentId = undefined;
    this.emit({ type: "reset", transcript: [] });
  }

  // ----- first run

  /**
   * The terminal login (docs/specs/2026-11-04-vscode-extension-design.md, section 7). Runs the
   * setup in a terminal, then restarts the agent (a replaced key needs a new process).
   */
  async runSetup(): Promise<void> {
    if (this.status !== "needs-setup" && this.status !== "ready") return;
    const command = this.command;
    const cwd = this.cwd;
    if (!command || cwd === undefined) return;
    const method = this.terminalMethod();
    const { exitCode } = await this.host.runSetupTerminal({
      command: command.command,
      args: [...command.args, ...method.args],
      env: method.env,
      cwd,
    });
    if (exitCode === 0) {
      await this.restart(true);
    } else {
      this.pushNotice("warning", "Setup did not finish.");
      this.emit(this.stateMessage());
    }
  }

  private terminalMethod(): TerminalAuthMethod {
    const methods = this.initResult?.authMethods ?? [];
    for (const method of methods) {
      const raw = method as unknown as Record<string, unknown>;
      if (raw.type !== "terminal" || typeof raw.id !== "string") continue;
      const args = Array.isArray(raw.args) ? raw.args.filter((a): a is string => typeof a === "string") : [];
      const env: Record<string, string> = {};
      if (isRecord(raw.env)) {
        for (const [key, value] of Object.entries(raw.env)) if (typeof value === "string") env[key] = value;
      }
      return { id: raw.id, args, env };
    }
    return { id: "pi-setup", args: SETUP_FALLBACK_ARGS, env: {} };
  }

  // ----- agent to client

  private onUpdate(notification: SessionNotification): void {
    if (this.sessionId === undefined) {
      if (this.opening) this.earlyUpdates.push(notification);
      return;
    }
    if (notification.sessionId !== this.sessionId) return;
    const update = notification.update as unknown as Record<string, unknown> & { sessionUpdate: string };
    switch (update.sessionUpdate) {
      case "agent_message_chunk":
      case "agent_thought_chunk":
        this.onChunk(update, update.sessionUpdate === "agent_thought_chunk");
        return;
      case "tool_call":
      case "tool_call_update":
        this.onToolUpdate(update);
        return;
      case "current_mode_update":
        if (this.mode && typeof update.currentModeId === "string") {
          this.mode = { ...this.mode, current: update.currentModeId };
          this.emit(this.stateMessage());
        }
        return;
      case "config_option_update":
        if (Array.isArray(update.configOptions)) {
          this.readConfigOptions(update.configOptions as SessionConfigOption[]);
          this.emit(this.stateMessage());
        }
        return;
      case "notice": {
        const title = typeof update.title === "string" ? update.title : "";
        const description = typeof update.description === "string" ? update.description : "";
        const text = [title, description].filter((part) => part !== "").join(": ");
        if (text !== "") this.pushNotice(update.severity === "error" ? "error" : update.severity === "warning" ? "warning" : "info", text);
        return;
      }
      default:
        if (!this.ignoredKinds.has(update.sessionUpdate)) {
          this.ignoredKinds.add(update.sessionUpdate);
          this.host.log.info(`ignoring session update kind ${update.sessionUpdate}`);
        }
    }
  }

  private onChunk(update: Record<string, unknown>, thought: boolean): void {
    const content = update.content;
    if (!isRecord(content) || content.type !== "text" || typeof content.text !== "string" || content.text === "") return;
    const text = content.text;
    let messageId = this.segmentId;
    let item = messageId === undefined ? undefined : this.items.find((i) => i.type === "agent" && i.id === messageId);
    if (!item || item.type !== "agent") {
      messageId = this.nextId("agent");
      this.segmentId = messageId;
      item = { type: "agent", id: messageId, text: "", thought: "" };
      this.items.push(item);
    }
    if (thought) {
      item.thought += text;
      this.emit({ type: "agentThought", messageId: item.id, text });
    } else {
      item.text += text;
      this.emit({ type: "agentText", messageId: item.id, text });
    }
  }

  private onToolUpdate(update: Record<string, unknown>): void {
    const view = this.tools.apply(update);
    if (!view) return;
    this.segmentId = undefined;
    const index = this.items.findIndex((item) => item.type === "tool" && item.toolCall.id === view.id);
    if (index >= 0) this.items[index] = { type: "tool", toolCall: view };
    else this.items.push({ type: "tool", toolCall: view });
    this.emit({ type: "toolCall", toolCall: view });
  }

  private onPermissionRequest(
    request: RequestPermissionRequest,
    signal: AbortSignal,
  ): Promise<RequestPermissionResponse> {
    if (request.sessionId !== this.sessionId) return Promise.resolve(cancelled());
    const requestId = this.nextId("permission");
    const view = this.permissionView(requestId, request);
    return new Promise<RequestPermissionResponse>((resolve) => {
      const entry: PendingPermission = {
        resolve,
        optionIds: new Set(view.options.map((option) => option.id)),
        view,
      };
      this.pending.set(requestId, entry);
      this.segmentId = undefined;
      this.items.push({ type: "permission", request: view });
      this.emit({ type: "permission", ...view });
      if (signal.aborted) {
        this.settlePermission(requestId, "cancelled");
      } else {
        signal.addEventListener("abort", () => this.settlePermission(requestId, "cancelled"), { once: true });
      }
    });
  }

  private permissionView(requestId: string, request: RequestPermissionRequest): PermissionRequestView {
    const toolCall = request.toolCall;
    const known = this.tools.get(toolCall.toolCallId);
    const rawInput = toolCall.rawInput ?? known?.rawInput;
    const options: PermissionOptionView[] = request.options.map((option) => ({
      id: option.optionId,
      name: option.name,
      kind: option.kind,
    }));
    const base = {
      requestId,
      toolCallId: toolCall.toolCallId,
      options,
      canDiff: false,
    };
    const title = known?.title ?? toolCall.title ?? "Tool call";
    if (looksLikeTrustRequest(toolCall.toolCallId, rawInput)) {
      if (hasTrustOptions(options)) {
        return { ...base, kind: "trust", title: toolCall.title ?? "loading this project's resources", summary: TRUST_NOTE };
      }
      return { ...base, kind: "tool", title: `Unexpected trust question: ${title}`, summary: "" };
    }
    return { ...base, kind: "tool", title, summary: summarizeInput(rawInput) };
  }

  /** The user's choice on a card. Only an option that the request listed counts. */
  answerPermission(requestId: string, optionId: string): boolean {
    const entry = this.pending.get(requestId);
    if (!entry || !entry.optionIds.has(optionId)) return false;
    this.settlePermission(requestId, "selected", optionId);
    return true;
  }

  private settlePermission(requestId: string, outcome: PermissionOutcome, optionId?: string): void {
    const entry = this.pending.get(requestId);
    if (!entry) return;
    this.pending.delete(requestId);
    const item = this.items.find((i) => i.type === "permission" && i.request.requestId === requestId);
    const done = optionId === undefined ? { outcome } : { outcome, optionId };
    if (item?.type === "permission") item.done = done;
    entry.resolve(
      outcome === "selected" && optionId !== undefined
        ? { outcome: { outcome: "selected", optionId } }
        : cancelled(),
    );
    this.emit({ type: "permissionDone", requestId, ...done });
  }

  private cancelAllPermissions(): void {
    for (const requestId of [...this.pending.keys()]) this.settlePermission(requestId, "cancelled");
  }

  // ----- helpers

  private setStatus(status: Status, detail?: string): void {
    this.status = status;
    this.detail = detail;
    this.emit(this.stateMessage());
  }

  private setError(text: string): void {
    this.setStatus("error", text);
  }

  private pushNotice(level: NoticeLevel, text: string): void {
    this.segmentId = undefined;
    this.items.push({ type: "notice", level, text });
    this.emit({ type: "notice", level, text });
  }

  private nextId(prefix: string): string {
    return `${prefix}-${++this.counter}`;
  }

  private clearCancelTimer(): void {
    if (this.cancelTimer) clearTimeout(this.cancelTimer);
    this.cancelTimer = undefined;
  }

  private emit(message: HostMessage): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(message);
      } catch (error) {
        this.host.log.error(`a view listener failed: ${messageOf(error)}`);
      }
    }
  }
}

function cancelled(): RequestPermissionResponse {
  return { outcome: { outcome: "cancelled" } };
}

function looksLikeTrustRequest(toolCallId: string, rawInput: unknown): boolean {
  return (
    toolCallId.startsWith(TRUST_ID_PREFIX) &&
    isRecord(rawInput) &&
    "project" in rawInput &&
    "resources" in rawInput
  );
}

/** The agent's trust question has exactly two options, and no `allow_once` (trust_prompt.py). */
function hasTrustOptions(options: PermissionOptionView[]): boolean {
  if (options.length !== 2) return false;
  const reject = options.find((o) => o.id === TRUST_REJECT_ID && o.kind === "reject_once");
  const accept = options.find((o) => o.id === TRUST_ACCEPT_ID && o.kind === "allow_always");
  return reject !== undefined && accept !== undefined;
}

function flattenSelect(option: SessionConfigOption): { id: string; name: string }[] {
  const raw = (option as unknown as { options?: unknown }).options;
  const out: { id: string; name: string }[] = [];
  const visit = (entry: unknown) => {
    if (!isRecord(entry)) return;
    if (typeof entry.value === "string") {
      out.push({ id: entry.value, name: typeof entry.name === "string" ? entry.name : entry.value });
    } else if (Array.isArray(entry.options)) {
      entry.options.forEach(visit);
    }
  };
  if (Array.isArray(raw)) raw.forEach(visit);
  return out;
}

function fileExistsSync(file: string): boolean {
  try {
    return statSync(file).isFile();
  } catch {
    return false;
  }
}
