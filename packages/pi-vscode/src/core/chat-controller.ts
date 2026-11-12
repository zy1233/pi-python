import { statSync } from "node:fs";

import { AcpConnection, AuthRequiredError, isMethodNotFound } from "./acp-connection.js";
import type {
  InitializeResponse,
  NewSessionResponse,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionConfigOption,
  SessionNotification,
} from "./acp-connection.js";
import { AgentProcess, type StopTimings } from "./agent-process.js";
import { DiffTracker, type DiffInfo } from "./diff-tracker.js";
import { classifyAuthRequired, classifyTurnFailure, failureNotice, setupText } from "./first-run.js";
import type { Host } from "./host.js";
import {
  prepareBundledBinary,
  resolveAgentCommand,
  type AgentCommand,
} from "./locate.js";
import {
  ALWAYS_APPROVE,
  type HostMessage,
  type ModeState,
  type NoticeAction,
  type NoticeLevel,
  type PermissionModeName,
  type PermissionOptionView,
  type PermissionOutcome,
  type PermissionRequestView,
  type SetupInfo,
  type StartModeName,
  type StateMessage,
  type Status,
  type ToolCallView,
  type TranscriptItem,
} from "./protocol.js";
import {
  MAX_LIST_PAGES,
  displayTitle,
  rowsFromPages,
  sameFolder,
  type RawListedSession,
  type SessionListState,
  type SessionRow,
} from "./session-list.js";
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
const MODE_CONFIRM_MS = 5000;

export const ALWAYS_APPROVE_WARNING = {
  message: "Turn on Always approve?",
  detail:
    "While it is on, every tool call runs with no question. This includes bash: the agent can change or delete " +
    "files and run any command in this folder, and it can read and send what your account can reach. " +
    "It stays on for this chat only. A new chat, a restart of the agent and a restart of VS Code start in " +
    "Ask or Auto again.",
  action: "Turn on Always approve",
};

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
  private stuckModeNoticeShown = false;
  private setupInfo: SetupInfo | undefined;
  private settingUp = false;
  private projectTrustRefused = false;
  private modeWaiters: { mode: string; resolve: (done: boolean) => void; timer: NodeJS.Timeout }[] = [];
  private turnStart = 0;
  private sessionList: SessionListState = { kind: "idle" };
  private deleteSupported = true;
  private refreshing: Promise<void> | undefined;
  private refreshAgain = false;
  private readonly sessionListeners = new Set<() => void>();

  private items: TranscriptItem[] = [];
  private readonly tools = new ToolCallStore();
  private readonly diffs: DiffTracker;
  private readonly pending = new Map<string, PendingPermission>();
  private readonly listeners = new Set<(message: HostMessage) => void>();
  private readonly ignoredKinds = new Set<string>();

  constructor(
    private readonly host: Host,
    private readonly options: ControllerOptions = {},
  ) {
    this.diffs = new DiffTracker(host, () => this.cwd);
  }

  // ----- subscription and snapshots

  onMessage(listener: (message: HostMessage) => void): { dispose(): void } {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  }

  /** The panel of past sessions subscribes here. */
  onSessionsChanged(listener: () => void): { dispose(): void } {
    this.sessionListeners.add(listener);
    return { dispose: () => this.sessionListeners.delete(listener) };
  }

  stateMessage(): StateMessage {
    const message: StateMessage = {
      type: "state",
      status: this.status,
      canRestart: this.status !== "starting",
      showThinking: this.host.settings().showThinking,
    };
    if (this.detail !== undefined) message.detail = this.detail;
    if (this.initResult && this.command) {
      message.agent = {
        version: this.initResult.agentInfo?.version ?? "unknown",
        source: this.command.source,
      };
    }
    if (this.cwd !== undefined) message.cwd = this.cwd;
    if (this.mode) message.mode = this.visibleMode(this.mode);
    if (this.model !== undefined) message.model = this.model;
    if (this.status === "needs-setup" && this.setupInfo) message.setup = this.setupInfo;
    return message;
  }

  /** The menu: the options of the agent, and `always-approve` when it can be chosen. */
  private visibleMode(mode: ModeState): ModeState {
    const options = [...mode.options];
    if (options.length > 0 && !options.some((o) => o.id === ALWAYS_APPROVE) && this.alwaysApproveOffered().ok) {
      options.push({ id: ALWAYS_APPROVE, name: "Always approve" });
    }
    return { current: mode.current, options };
  }

  /**
   * Always approve is offered only in a trusted workspace (VS Code Workspace Trust) and only if the
   * user did not refuse the agent's own project-trust question in this agent process (project trust,
   * invariant 9), and only if the agent has the `mode` option (v0.9.0 and later).
   */
  alwaysApproveOffered(): { ok: boolean; reason?: string } {
    if (!this.host.isTrusted()) return { ok: false, reason: "this workspace is not trusted (Restricted Mode)." };
    if (this.projectTrustRefused) {
      return { ok: false, reason: "you did not trust this project when the agent asked. Restart the agent to be asked again." };
    }
    if (!this.mode || this.mode.options.length === 0) return { ok: false, reason: "this agent has no mode option." };
    return { ok: true };
  }

  transcript(): TranscriptItem[] {
    return this.items.map((item) => structuredClone(item));
  }

  /** The diff of a tool call (the result if the call has run, else the proposal), or `undefined`. */
  diffFor(toolCallId: string): DiffInfo | undefined {
    return this.diffs.info(toolCallId);
  }

  /** The texts of the diffs: the view reads them through a content provider. */
  get diffStore(): DiffTracker["store"] {
    return this.diffs.store;
  }

  /** A setting that the view shows changed (`zycode.showThinking`). */
  refreshSettings(): void {
    this.emit(this.stateMessage());
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
    this.projectTrustRefused = false;
    this.deleteSupported = true;
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
        this.setupInfo = classifyAuthRequired(error.reason);
        this.setStatus("needs-setup", setupText(this.setupInfo));
        this.refreshSessionList();
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
    this.refreshSessionList();
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
    this.resolveModeWaiters();
  }

  private resolveModeWaiters(): void {
    const current = this.mode?.current;
    for (const waiter of [...this.modeWaiters]) {
      if (waiter.mode !== current) continue;
      clearTimeout(waiter.timer);
      this.modeWaiters = this.modeWaiters.filter((w) => w !== waiter);
      waiter.resolve(true);
    }
  }

  /** Resolves `true` when the agent reports `mode` for the session, `false` after the time. */
  private waitForMode(mode: string, ms: number): Promise<boolean> {
    return new Promise((resolve) => {
      const waiter = {
        mode,
        resolve,
        timer: setTimeout(() => {
          this.modeWaiters = this.modeWaiters.filter((w) => w !== waiter);
          resolve(false);
        }, ms),
      };
      this.modeWaiters.push(waiter);
    });
  }

  private async applyPermissionSetting(): Promise<void> {
    const wanted = this.host.settings().permissionMode;
    await this.setSessionMode(wanted, false);
  }

  /**
   * Sets the permission mode of the open session. Used for the setting (`ask`, `auto`) and for the
   * menu of the view (also `always-approve`).
   */
  async applyPermissionMode(mode: PermissionModeName): Promise<void> {
    if (mode === ALWAYS_APPROVE) await this.enterAlwaysApprove();
    else await this.setSessionMode(mode, true);
    this.emit(this.stateMessage());
  }

  /** `ask` or `auto`. Leaving `always-approve` also puts the start mode of the agent back. */
  private async setSessionMode(wanted: StartModeName, announce: boolean): Promise<void> {
    const conn = this.conn;
    const sessionId = this.sessionId;
    if (!conn || !sessionId) return;
    const mode = this.mode;
    const canChoose = mode !== undefined && mode.options.some((option) => option.id === wanted);
    if (!canChoose) {
      if (mode?.current === ALWAYS_APPROVE && !this.stuckModeNoticeShown) {
        this.stuckModeNoticeShown = true;
        this.pushNotice(
          "warning",
          "The agent is in always-approve mode, and this agent version has no mode option, so zycode cannot change it. " +
            "Set permission in agent.toml and restart the agent.",
        );
      } else if (wanted === "auto" && !this.modeNoticeShown) {
        this.modeNoticeShown = true;
        this.pushNotice("warning", "This agent version has no Auto mode.");
      }
      return;
    }
    if (mode.current === wanted) return;
    const wasAlwaysApprove = mode.current === ALWAYS_APPROVE;
    try {
      const options = await conn.setConfigOption(sessionId, "mode", wanted);
      if (options.length > 0) this.readConfigOptions(options);
      else this.mode = { ...mode, current: wanted };
      if (wasAlwaysApprove) {
        // The agent keeps the last mode it was given as the start mode of later sessions.
        await conn.notifyPermissionMode(wanted).catch((error: unknown) => {
          this.host.log.warn(`could not reset the start mode: ${messageOf(error)}`);
        });
      }
      if (announce) this.pushNotice("info", `Permission mode: ${wanted}.`);
    } catch (error) {
      this.pushNotice("warning", `Could not set the permission mode: ${messageOf(error)}`);
    }
  }

  private async enterAlwaysApprove(): Promise<void> {
    const conn = this.conn;
    const sessionId = this.sessionId;
    const mode = this.mode;
    if (!conn || !sessionId || !mode || mode.current === ALWAYS_APPROVE) return;
    const offered = this.alwaysApproveOffered();
    if (!offered.ok) {
      this.pushNotice("warning", `Always approve is not available: ${offered.reason}`);
      return;
    }
    if (this.status !== "ready") {
      this.pushNotice("info", "Wait until the answer ends, then turn on Always approve.");
      return;
    }
    const generation = this.generation;
    if (!(await this.host.confirm(ALWAYS_APPROVE_WARNING))) return;
    if (generation !== this.generation || this.sessionId !== sessionId) return;
    const stillOffered = this.alwaysApproveOffered();
    if (!stillOffered.ok) {
      this.pushNotice("warning", `Always approve is not available: ${stillOffered.reason}`);
      return;
    }
    const confirmed = this.waitForMode(ALWAYS_APPROVE, MODE_CONFIRM_MS);
    try {
      await conn.notifyPermissionMode(ALWAYS_APPROVE);
    } catch (error) {
      this.pushNotice("warning", `Could not set the permission mode: ${messageOf(error)}`);
      return;
    }
    if (await confirmed) {
      this.pushNotice("warning", "Always approve is on: every tool call runs with no question, bash too.");
    } else {
      this.pushNotice("warning", "The agent did not confirm Always approve. zycode shows the mode that the agent reports.");
    }
  }

  /** Before a new session: a session that starts must not inherit `always-approve`. */
  private async leaveAlwaysApprove(): Promise<void> {
    if (this.mode?.current === ALWAYS_APPROVE) await this.setSessionMode(this.host.settings().permissionMode, false);
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
    this.turnStart = this.items.length;
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
    this.noteTurnFailure();
    this.emit({ type: "turnEnd", stopReason });
    if (this.status === "busy" || this.status === "cancelling") this.setStatus("ready");
    this.refreshSessionList();
  }

  /** A failed model call is an agent message `Error: ...`. A known kind gets a hint and a button. */
  private noteTurnFailure(): void {
    const text = this.items
      .slice(this.turnStart)
      .flatMap((item) => (item.type === "agent" ? [item.text] : []))
      .join("\n");
    const kind = classifyTurnFailure(text);
    if (kind === undefined) return;
    const hint = failureNotice(kind);
    this.pushNotice("warning", hint.text, hint.actions);
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
    await this.leaveAlwaysApprove();
    const previous = this.sessionId;
    this.sessionId = undefined;
    this.resetTranscript();
    await this.closeSessionQuietly(previous);
    await this.openSession();
  }

  private async closeSessionQuietly(sessionId: string | undefined): Promise<void> {
    const conn = this.conn;
    if (!conn || !sessionId || this.initResult?.agentCapabilities?.sessionCapabilities?.close === undefined) return;
    await conn.closeSession(sessionId).catch((error) => {
      this.host.log.warn(`session/close failed: ${messageOf(error)}`);
    });
  }

  private resetTranscript(): void {
    this.items = [];
    this.tools.clear();
    this.diffs.clear();
    this.segmentId = undefined;
    this.emit({ type: "reset", transcript: [] });
  }

  // ----- first run

  /**
   * The terminal login (docs/specs/2026-11-04-vscode-extension-design.md, section 7). Runs the
   * setup in a terminal, then restarts the agent (a replaced key needs a new process). It runs
   * from any state that has a program to run, so the guide and the command palette can open it
   * again later.
   */
  async runSetup(): Promise<void> {
    if (this.settingUp) return;
    if (this.status === "starting" || this.status === "busy" || this.status === "cancelling" || this.status === "unavailable") return;
    const command = await this.commandForSetup();
    if (!command) return;
    const method = this.terminalMethod();
    this.settingUp = true;
    let exitCode: number | undefined;
    try {
      ({ exitCode } = await this.host.runSetupTerminal({
        command: command.command,
        args: [...command.args, ...method.args],
        env: method.env,
        ...(this.cwd !== undefined ? { cwd: this.cwd } : {}),
      }));
    } finally {
      this.settingUp = false;
    }
    if (exitCode === 0) {
      await this.restart(true);
    } else {
      this.pushNotice("warning", "Setup did not finish. Run it again from the chat or from the command palette (zycode: Set up model and API key).");
      this.emit(this.stateMessage());
    }
  }

  private async commandForSetup(): Promise<AgentCommand | undefined> {
    if (this.command) return this.command;
    const settings = this.host.settings();
    const resolved = resolveAgentCommand({
      agentPath: settings.agentPath,
      agentArgs: settings.agentArgs,
      extensionPath: this.host.extensionPath,
      platform: this.host.platform,
      exists: (file) => fileExistsSync(file),
    });
    if (!resolved.ok) {
      this.pushNotice("error", resolved.reason);
      return undefined;
    }
    if (resolved.command.source === "bundled") {
      const problem = await prepareBundledBinary(resolved.command.command, this.host.platform);
      if (problem) this.host.log.warn(problem);
    }
    this.command = resolved.command;
    return resolved.command;
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

  // ----- past sessions

  /** What the panel of past sessions shows. */
  get sessions(): SessionListState {
    return this.sessionList;
  }

  /** The id of the open session, or `undefined` (none yet, or one that is opening). */
  get currentSessionId(): string | undefined {
    return this.sessionId;
  }

  /** `false` after an agent has answered that it has no `pi/session/delete`. */
  get canDeleteSessions(): boolean {
    return this.deleteSupported;
  }

  /** Reads the list again. Calls that come while a read runs make one more read after it. */
  refreshSessionList(): void {
    void this.refreshSessions();
  }

  refreshSessions(): Promise<void> {
    if (this.refreshing) {
      this.refreshAgain = true;
      return this.refreshing;
    }
    const run = async (): Promise<void> => {
      do {
        this.refreshAgain = false;
        await this.readSessionList();
      } while (this.refreshAgain);
    };
    this.refreshing = run().finally(() => {
      this.refreshing = undefined;
    });
    return this.refreshing;
  }

  private setSessions(state: SessionListState): void {
    this.sessionList = state;
    for (const listener of [...this.sessionListeners]) {
      try {
        listener();
      } catch (error) {
        this.host.log.error(`a list listener failed: ${messageOf(error)}`);
      }
    }
  }

  private offlineList(status: Status): SessionListState {
    switch (status) {
      case "no-folder":
        return { kind: "unavailable", reason: "Open a folder to see its past chats." };
      case "untrusted":
        return { kind: "unavailable", reason: "Past chats are not available in Restricted Mode." };
      case "unavailable":
      case "error":
        return { kind: "unavailable", reason: "The agent is not running. See the chat view." };
      default:
        return { kind: "idle" };
    }
  }

  private async readSessionList(): Promise<void> {
    const conn = this.conn;
    const cwd = this.cwd;
    const generation = this.generation;
    if (!conn || cwd === undefined || !this.initResult) {
      this.setSessions(this.offlineList(this.status));
      return;
    }
    if (this.initResult.agentCapabilities?.sessionCapabilities?.list === undefined) {
      this.setSessions({ kind: "unavailable", reason: "This agent cannot list sessions." });
      return;
    }
    const before = this.sessionList;
    this.setSessions({ kind: "loading", rows: before.kind === "ready" || before.kind === "loading" ? before.rows : [] });
    try {
      const pages: RawListedSession[][] = [];
      const seen = new Set<string>();
      let cursor: string | undefined;
      let truncated = false;
      for (let page = 0; page < MAX_LIST_PAGES; page++) {
        const response = await conn.listSessions(cwd, cursor);
        pages.push(response.sessions as unknown as RawListedSession[]);
        const next = response.nextCursor ?? undefined;
        if (next === undefined || next === null || next === "") break;
        if (seen.has(next) || page === MAX_LIST_PAGES - 1) {
          truncated = true;
          break;
        }
        seen.add(next);
        cursor = next;
      }
      if (generation !== this.generation) return;
      const rows = rowsFromPages(pages, cwd, this.host.platform);
      this.setSessions(rows.length === 0 ? { kind: "empty" } : { kind: "ready", rows, truncated });
    } catch (error) {
      if (generation !== this.generation) return;
      this.host.log.warn(`session/list failed: ${messageOf(error)}`);
      this.setSessions({ kind: "error", message: messageOf(error) });
    }
  }

  private listedRow(id: string): SessionRow | undefined {
    const state = this.sessionList;
    if (state.kind !== "ready" && state.kind !== "loading") return undefined;
    return state.rows.find((row) => row.id === id);
  }

  /**
   * Opens a past session of this folder: `session/load`, and the agent replays the history. Only a
   * session of the list can be opened, and the session must say that it works in this folder.
   */
  async openPastSession(id: string): Promise<void> {
    const conn = this.conn;
    const cwd = this.cwd;
    if (!conn || cwd === undefined) return;
    if (this.status !== "ready") {
      this.pushNotice("info", "Wait until the answer ends (or finish the setup), then open a past chat.");
      return;
    }
    if (id === this.sessionId) return;
    if (!this.listedRow(id)) {
      this.pushNotice("warning", "This chat is not in the list of this folder any more.");
      this.refreshSessionList();
      return;
    }
    await this.leaveAlwaysApprove();
    const generation = this.generation;
    const previous = this.sessionId;
    this.sessionId = undefined;
    this.resetTranscript();
    this.opening = true;
    this.earlyUpdates = [];
    this.setStatus("starting", "Opening the chat");
    await this.closeSessionQuietly(previous);
    let response: Awaited<ReturnType<AcpConnection["loadSession"]>>;
    try {
      response = await conn.loadSession(cwd, id);
    } catch (error) {
      this.opening = false;
      this.earlyUpdates = [];
      if (generation !== this.generation) return;
      this.pushNotice("error", `Could not open the chat: ${messageOf(error)}`);
      await this.openSession();
      return;
    }
    if (generation !== this.generation) return;
    const reported = isRecord(response._meta) ? response._meta["pi/cwd"] : undefined;
    if (typeof reported === "string" && !sameFolder(reported, cwd, this.host.platform)) {
      this.opening = false;
      this.earlyUpdates = [];
      await this.closeSessionQuietly(id);
      this.pushNotice("error", "That chat belongs to another folder. It was not opened.");
      this.refreshSessionList();
      await this.openSession();
      return;
    }
    this.sessionId = id;
    this.readConfigOptions(response.configOptions ?? [], response.modes?.currentModeId);
    const held = this.earlyUpdates;
    this.earlyUpdates = [];
    this.opening = false;
    for (const notification of held) this.onUpdate(notification);
    await this.applyPermissionSetting();
    if (generation !== this.generation) return;
    this.setStatus("ready");
    this.refreshSessionList();
  }

  /** Deletes a past session of this folder, after the user confirmed. `pi/session/delete` (register item X1). */
  async deleteSession(id: string): Promise<void> {
    const conn = this.conn;
    if (!conn) return;
    if (!this.deleteSupported) {
      this.pushNotice("warning", "This agent version cannot delete a chat.");
      return;
    }
    if (this.status !== "ready") {
      this.pushNotice("info", "Wait until the answer ends, then delete the chat.");
      return;
    }
    const row = this.listedRow(id);
    if (!row) {
      this.refreshSessionList();
      return;
    }
    const current = id === this.sessionId;
    const detail =
      `"${displayTitle(row)}" is deleted from this computer. This cannot be undone.` +
      (current ? " This chat is open now: it is cleared, and a new chat starts." : "");
    if (!(await this.host.confirm({ message: "Delete this chat?", detail, action: "Delete" }))) return;
    if (this.status !== "ready" || this.conn !== conn) return;
    if (current) await this.leaveAlwaysApprove();
    try {
      await conn.deleteSession(id);
    } catch (error) {
      if (isMethodNotFound(error)) {
        this.deleteSupported = false;
        this.pushNotice("warning", "This agent version cannot delete a chat.");
      } else {
        this.pushNotice("error", `Could not delete the chat: ${messageOf(error)}`);
      }
      this.refreshSessionList();
      return;
    }
    if (current) {
      this.sessionId = undefined;
      this.resetTranscript();
      await this.openSession();
    } else {
      this.refreshSessionList();
    }
  }

  // ----- agent to client

  private onUpdate(notification: SessionNotification): void {
    if (this.sessionId === undefined) {
      if (this.opening) this.earlyUpdates.push(notification);
      return;
    }
    if (notification.sessionId !== this.sessionId) return;
    const update = notification.update as unknown as Record<string, unknown> & { sessionUpdate: string };
    const replay = isRecord(update._meta) && update._meta.isReplay === true;
    switch (update.sessionUpdate) {
      case "user_message_chunk":
        // Only the replay of `session/load` has these: a live prompt is shown when it is sent.
        if (replay) this.onReplayedUser(update);
        return;
      case "agent_message_chunk":
      case "agent_thought_chunk":
        this.onChunk(update, update.sessionUpdate === "agent_thought_chunk");
        return;
      case "tool_call":
      case "tool_call_update":
        this.onToolUpdate(update, replay);
        return;
      case "current_mode_update":
        if (this.mode && typeof update.currentModeId === "string") {
          this.mode = { ...this.mode, current: update.currentModeId };
          this.resolveModeWaiters();
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

  private onReplayedUser(update: Record<string, unknown>): void {
    const content = update.content;
    if (!isRecord(content) || content.type !== "text" || typeof content.text !== "string" || content.text === "") return;
    this.segmentId = undefined;
    const id = this.nextId("user");
    this.items.push({ type: "user", id, text: content.text });
    this.emit({ type: "userMessage", id, text: content.text });
  }

  private onToolUpdate(update: Record<string, unknown>, replay = false): void {
    // The files may have changed since: a replayed call gets no diff.
    if (!replay) this.diffs.observe(update);
    const view = this.tools.apply(update);
    if (!view) return;
    this.segmentId = undefined;
    this.putToolItem(view);
    if (!replay && (view.status === "completed" || view.status === "failed")) {
      const generation = this.generation;
      void this.diffs
        .finish(view.id, view.status, update.rawOutput)
        .then((available) => {
          if (generation !== this.generation) return;
          const current = this.tools.get(view.id);
          if (!current || current.canDiff === available) return;
          this.putToolItem({ ...current, canDiff: available });
        })
        .catch((error: unknown) => this.host.log.warn(`diff failed: ${messageOf(error)}`));
    }
  }

  private putToolItem(view: ToolCallView): void {
    const stored = this.tools.update(view.id, view);
    const index = this.items.findIndex((item) => item.type === "tool" && item.toolCall.id === stored.id);
    if (index >= 0) this.items[index] = { type: "tool", toolCall: stored };
    else this.items.push({ type: "tool", toolCall: stored });
    this.emit({ type: "toolCall", toolCall: stored });
  }

  private async onPermissionRequest(
    request: RequestPermissionRequest,
    signal: AbortSignal,
  ): Promise<RequestPermissionResponse> {
    if (request.sessionId !== this.sessionId) return cancelled();
    // The pre-image of a write is read before the card shows, so in `ask` mode it is taken before the tool runs.
    const proposal = await this.diffs.propose(request.toolCall.toolCallId).catch(() => undefined);
    if (request.sessionId !== this.sessionId) return cancelled();
    const requestId = this.nextId("permission");
    const view = this.permissionView(requestId, request, proposal);
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

  private permissionView(
    requestId: string,
    request: RequestPermissionRequest,
    proposal?: { canDiff: boolean; note?: string },
  ): PermissionRequestView {
    const toolCall = request.toolCall;
    const known = this.tools.get(toolCall.toolCallId);
    const rawInput = toolCall.rawInput ?? known?.rawInput;
    const options: PermissionOptionView[] = request.options.map((option) => ({
      id: option.optionId,
      name: option.name,
      kind: option.kind,
    }));
    const base: Pick<PermissionRequestView, "requestId" | "toolCallId" | "options" | "canDiff" | "diffNote"> = {
      requestId,
      toolCallId: toolCall.toolCallId,
      options,
      canDiff: proposal?.canDiff ?? false,
    };
    if (proposal?.note !== undefined) base.diffNote = proposal.note;
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
    if (entry.view.kind === "trust") this.onTrustAnswer(outcome === "selected" && optionId === TRUST_ACCEPT_ID);
    entry.resolve(
      outcome === "selected" && optionId !== undefined
        ? { outcome: { outcome: "selected", optionId } }
        : cancelled(),
    );
    this.emit({ type: "permissionDone", requestId, ...done });
  }

  /** The user's answer to the agent's own project-trust question: a refusal takes Always approve away. */
  private onTrustAnswer(trusted: boolean): void {
    this.projectTrustRefused = !trusted;
    if (trusted) return;
    if (this.mode?.current === ALWAYS_APPROVE) {
      this.pushNotice("warning", "Always approve is off: you did not trust this project.");
      void this.leaveAlwaysApprove().then(() => this.emit(this.stateMessage()));
    }
  }

  private cancelAllPermissions(): void {
    for (const requestId of [...this.pending.keys()]) this.settlePermission(requestId, "cancelled");
  }

  // ----- helpers

  private setStatus(status: Status, detail?: string): void {
    this.status = status;
    this.detail = detail;
    if (status === "stopped" || status === "no-folder" || status === "untrusted" || status === "unavailable" || status === "error") {
      this.setSessions(this.offlineList(status));
    }
    this.emit(this.stateMessage());
  }

  private setError(text: string): void {
    this.setStatus("error", text);
  }

  private pushNotice(level: NoticeLevel, text: string, actions?: NoticeAction[]): void {
    this.segmentId = undefined;
    if (actions) {
      this.items.push({ type: "notice", level, text, actions });
      this.emit({ type: "notice", level, text, actions });
    } else {
      this.items.push({ type: "notice", level, text });
      this.emit({ type: "notice", level, text });
    }
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
