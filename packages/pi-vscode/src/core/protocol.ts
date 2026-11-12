/**
 * The messages between the chat view (webview) and the host, and the shapes the controller keeps.
 * Design: docs/specs/2026-11-04-vscode-extension-design.md, section 6.
 *
 * Both sides import this file. The host checks every message from the webview with
 * `parseWebviewMessage`: a page script can send anything.
 */

export type Status =
  | "stopped"
  | "starting"
  | "ready"
  | "busy"
  | "cancelling"
  | "needs-setup"
  | "error"
  | "no-folder"
  | "untrusted"
  | "unavailable";

/** The modes of the mode menu. `zycode.permissionMode` offers `ask` and `auto` only. */
export type PermissionModeName = "ask" | "auto" | "always-approve";

/** The modes that a start can have: the setting never holds `always-approve`. */
export type StartModeName = "ask" | "auto";

export const ALWAYS_APPROVE = "always-approve" as const;

export interface ModeState {
  /** The mode the session is in. It can be `always-approve`, also when `agent.toml` sets it. */
  current: string;
  /** The values the menu offers. Empty when the agent has no `mode` option. `always-approve` is in the list only when it can be chosen. */
  options: { id: string; name: string }[];
}

/** The levels of `thinking_level` in agent.toml. */
export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh";

export interface ThinkingLevelOption {
  id: ThinkingLevel;
  name: string;
  description: string;
}

export interface ThinkingState {
  /** The level the running agent uses. Absent when the file cannot be read. */
  current?: ThinkingLevel;
  levels: ThinkingLevelOption[];
  /** Set when the picker is off, with the reason. */
  disabled?: string;
  /** Something the user should know about the file (for example an invalid value). */
  note?: string;
}

/** Why the agent has no model yet, from its `auth_required` answer to `session/new`. */
export type SetupReason = "no-model" | "no-key" | "unknown";

export interface SetupInfo {
  reason: SetupReason;
  /** The variable that has no value (`no-key`). */
  variable?: string;
}

export interface StateMessage {
  type: "state";
  status: Status;
  detail?: string;
  agent?: { version: string; source: "setting" | "bundled" };
  cwd?: string;
  mode?: ModeState;
  model?: string;
  /** The thinking level picker. Absent while the agent is not up. */
  thinking?: ThinkingState;
  /** Set while `status` is `needs-setup`: what is missing. */
  setup?: SetupInfo;
  canRestart: boolean;
  /** `zycode.showThinking`: show the thinking blocks (collapsed). */
  showThinking?: boolean;
}

export type ToolStatus = "pending" | "in_progress" | "completed" | "failed";

export interface ToolCallView {
  id: string;
  title: string;
  kind: string;
  status: ToolStatus;
  rawInput?: unknown;
  /** The last text block of the latest update. For `bash` it is the whole output so far. */
  text?: string;
  terminal?: { output: string; exitCode?: number };
  /** The host can show a diff for this call (`showDiff` with `id`). */
  canDiff?: boolean;
}

export interface PermissionOptionView {
  id: string;
  name: string;
  kind: string;
}

export interface PermissionRequestView {
  requestId: string;
  kind: "tool" | "trust";
  toolCallId?: string;
  title: string;
  /** The whole command for `bash`, the path for `read`, `edit` and `write`, else empty. */
  summary: string;
  options: PermissionOptionView[];
  canDiff: boolean;
  /** Why there is no diff, for a `write` or `edit` call that has none. */
  diffNote?: string;
}

export type PermissionOutcome = "selected" | "cancelled";

export type NoticeActionId = "runSetup" | "openGuide" | "openAgentConfig" | "openLog";

export interface NoticeAction {
  id: NoticeActionId;
  label: string;
}

export type TranscriptItem =
  | { type: "user"; id: string; text: string }
  | { type: "agent"; id: string; text: string; thought: string }
  | { type: "tool"; toolCall: ToolCallView }
  | { type: "permission"; request: PermissionRequestView; done?: { outcome: PermissionOutcome; optionId?: string } }
  | { type: "notice"; level: NoticeLevel; text: string; actions?: NoticeAction[] };

export type NoticeLevel = "info" | "warning" | "error";

export type HostMessage =
  | StateMessage
  | { type: "reset"; transcript: TranscriptItem[] }
  | { type: "userMessage"; id: string; text: string }
  | { type: "agentText"; messageId: string; text: string }
  | { type: "agentThought"; messageId: string; text: string }
  | { type: "toolCall"; toolCall: ToolCallView }
  | ({ type: "permission" } & PermissionRequestView)
  | { type: "permissionDone"; requestId: string; outcome: PermissionOutcome; optionId?: string }
  | { type: "notice"; level: NoticeLevel; text: string; actions?: NoticeAction[] }
  | { type: "turnEnd"; stopReason: string }
  | { type: "diffState"; toolCallId: string; available: boolean };

export type WebviewMessage =
  | { type: "ready" }
  | { type: "send"; text: string }
  | { type: "cancel" }
  | { type: "newChat" }
  | { type: "permissionAnswer"; requestId: string; optionId: string }
  | { type: "showDiff"; toolCallId: string }
  | { type: "setMode"; value: PermissionModeName }
  | { type: "setThinking"; value: ThinkingLevel }
  | { type: "runSetup" }
  | { type: "openGuide" }
  | { type: "openAgentConfig" }
  | { type: "restartAgent" }
  | { type: "openLog" }
  | { type: "openLink"; url: string };

const MAX_PROMPT_CHARS = 200_000;
const MAX_ID_CHARS = 512;
const MAX_URL_CHARS = 4096;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function shortString(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

/** The message, if `value` is a well-formed message of the webview. Unknown types give `undefined`. */
export function parseWebviewMessage(value: unknown): WebviewMessage | undefined {
  if (!isRecord(value) || typeof value.type !== "string") return undefined;
  switch (value.type) {
    case "ready":
    case "cancel":
    case "newChat":
    case "runSetup":
    case "openGuide":
    case "openAgentConfig":
    case "restartAgent":
    case "openLog":
      return { type: value.type };
    case "send":
      if (typeof value.text !== "string") return undefined;
      if (value.text.trim() === "" || value.text.length > MAX_PROMPT_CHARS) return undefined;
      return { type: "send", text: value.text };
    case "permissionAnswer":
      if (!shortString(value.requestId, MAX_ID_CHARS) || !shortString(value.optionId, MAX_ID_CHARS)) {
        return undefined;
      }
      return { type: "permissionAnswer", requestId: value.requestId, optionId: value.optionId };
    case "showDiff":
      if (!shortString(value.toolCallId, MAX_ID_CHARS)) return undefined;
      return { type: "showDiff", toolCallId: value.toolCallId };
    case "setMode":
      if (value.value !== "ask" && value.value !== "auto" && value.value !== "always-approve") return undefined;
      return { type: "setMode", value: value.value };
    case "setThinking":
      if (
        value.value !== "off" &&
        value.value !== "minimal" &&
        value.value !== "low" &&
        value.value !== "medium" &&
        value.value !== "high" &&
        value.value !== "xhigh"
      ) {
        return undefined;
      }
      return { type: "setThinking", value: value.value };
    case "openLink":
      if (!shortString(value.url, MAX_URL_CHARS)) return undefined;
      return { type: "openLink", url: value.url };
    default:
      return undefined;
  }
}

/** A link the host may open for the webview: `http`, `https` or `mailto`, and nothing else. */
export function isSafeLink(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:";
  } catch {
    return false;
  }
}
