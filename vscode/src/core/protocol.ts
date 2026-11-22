/**
 * The messages between the chat view (webview) and the host, and the shapes the controller keeps.
 * Design: docs/specs/2026-11-04-vscode-extension-design.md, section 6.
 *
 * Both sides import this file. The host checks every message from the webview with
 * `parseWebviewMessage`: a page script can send anything.
 */

import type { SlashCommandInfo } from "./composer.js";
import {
  MAX_MENTIONS,
  MAX_MENTION_QUERY_CHARS,
  normalizeRelativePath,
  type MentionAttachment,
  type MentionKind,
  type MentionRange,
  type RankedMention,
} from "./mentions.js";

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

export interface ModelState {
  current: string;
  options: { id: string; name: string }[];
}

/**
 * The levels of `thinking_level` in agent.toml. An agent knows `max` only from `MAX_LEVEL_SINCE` on
 * (`thinking.ts`): the menu gets the list for the agent that runs, and this type has all of them.
 */
export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

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

/**
 * What the agent said about the context window, in its ACP `usage_update`
 * (docs/specs/2026-11-13-agent-context-usage.md). The extension counts nothing itself: an agent that
 * sends no `usage_update` leaves this out, and the view then shows no context row.
 */
export interface UsageState {
  /** Tokens that are in the context now. */
  used: number;
  /** Size of the context window in tokens. Absent when the agent did not give a usable size. */
  size?: number;
  /** What the chat has cost, when the agent says so. */
  cost?: { amount: number; currency: string };
}

/** Times that the host measures (the agent sends none). Design: section 7d. */
export interface TurnState {
  /** Milliseconds since the prompt was sent. Present only while a turn runs. */
  elapsedMs?: number;
  /** How long the last turn took. Present when no turn runs and one has ended in this chat. */
  lastMs?: number;
}

export interface StateMessage {
  type: "state";
  status: Status;
  detail?: string;
  agent?: { version: string; source: "setting" | "bundled" };
  cwd?: string;
  mode?: ModeState;
  model?: string;
  models?: ModelState;
  commands?: SlashCommandInfo[];
  /** The context window, from the agent's `usage_update`. Absent until the agent sends one. */
  usage?: UsageState;
  /** The times of the turn. */
  turn?: TurnState;
  /** The thinking level picker. Absent while the agent is not up. */
  thinking?: ThinkingState;
  /** Set while `status` is `needs-setup`: what is missing. */
  setup?: SetupInfo;
  canRestart: boolean;
  /** `zycode.showThinking`: show the thinking blocks (collapsed). */
  showThinking?: boolean;
  /** Whether the agent advertised `promptCapabilities.image`. Absent before `initialize`. */
  supportsImages?: boolean;
  /** Number of pending permission/trust questions waiting for user input. Omitted when 0. */
  pendingPermissions?: number;
}

export type ToolStatus = "pending" | "in_progress" | "completed" | "failed";

export interface ToolCallView {
  id: string;
  title: string;
  kind: string;
  status: ToolStatus;
  rawInput?: unknown;
  /** The last text block of the latest update. Omitted when live terminal output is present on `terminal`. */
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

export const MAX_IMAGES = 4;
export const MAX_IMAGE_DATA_CHARS = 7_000_000;
export const ALLOWED_IMAGE_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp"] as const;
export type ImageMimeType = (typeof ALLOWED_IMAGE_MIMES)[number];

export interface ImageAttachment {
  mimeType: ImageMimeType;
  data: string;
  name?: string;
}

export type NoticeActionId = "runSetup" | "openGuide" | "openAgentConfig" | "openLog" | "saveAll";

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
  | { type: "toolCallOutput"; id: string; delta: string; status: ToolStatus }
  | ({ type: "permission" } & PermissionRequestView)
  | { type: "permissionDone"; requestId: string; outcome: PermissionOutcome; optionId?: string }
  | { type: "notice"; level: NoticeLevel; text: string; actions?: NoticeAction[] }
  | { type: "turnEnd"; stopReason: string }
  | { type: "diffState"; toolCallId: string; available: boolean }
  | { type: "mentionResults"; requestId: number; query: string; items: RankedMention[] }
  | { type: "insertMention"; item: MentionAttachment };

export type WebviewMessage =
  | { type: "ready" }
  | { type: "send"; text: string; mentions?: MentionAttachment[]; images?: ImageAttachment[] }
  | { type: "cancel" }
  | { type: "newChat" }
  | { type: "permissionAnswer"; requestId: string; optionId: string }
  | { type: "showDiff"; toolCallId: string }
  | { type: "setMode"; value: PermissionModeName }
  | { type: "setThinking"; value: ThinkingLevel }
  | { type: "setModel"; value: string }
  | { type: "queryMentions"; requestId: number; query: string }
  | { type: "runSetup" }
  | { type: "openGuide" }
  | { type: "openAgentConfig" }
  | { type: "restartAgent" }
  | { type: "openLog" }
  | { type: "saveAll" }
  | { type: "openFile"; path: string; line?: number }
  | { type: "copy"; text: string }
  | { type: "openLink"; url: string };

const MAX_PROMPT_CHARS = 200_000;
const MAX_ID_CHARS = 512;
const MAX_URL_CHARS = 4096;
const MAX_IMAGE_NAME_CHARS = 128;
const BASE64_PATTERN = /^[A-Za-z0-9+/=]+$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function shortString(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function parseMentionKind(value: unknown): MentionKind | undefined {
  if (value === "file" || value === "folder" || value === "selection") return value;
  return undefined;
}

function parseMentionRange(value: unknown): MentionRange | undefined {
  if (!isRecord(value)) return undefined;
  const { startLine, endLine } = value;
  if (
    typeof startLine !== "number" ||
    typeof endLine !== "number" ||
    !Number.isSafeInteger(startLine) ||
    !Number.isSafeInteger(endLine) ||
    startLine < 1 ||
    endLine < startLine
  ) {
    return undefined;
  }
  return { startLine, endLine };
}

function parseMentions(value: unknown): MentionAttachment[] | undefined {
  if (!Array.isArray(value) || value.length > MAX_MENTIONS) return undefined;
  const out: MentionAttachment[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) return undefined;
    const kind = parseMentionKind(entry.kind);
    if (!kind || typeof entry.path !== "string") return undefined;
    const path = normalizeRelativePath(entry.path, kind);
    if (!path) return undefined;
    if (kind === "selection") {
      const range = parseMentionRange(entry.range);
      if (!range) return undefined;
      out.push({ kind, path, range });
    } else {
      if (entry.range !== undefined) return undefined;
      out.push({ kind, path });
    }
  }
  return out;
}

function isAllowedImageMime(value: unknown): value is ImageMimeType {
  return typeof value === "string" && (ALLOWED_IMAGE_MIMES as readonly string[]).includes(value);
}

function parseImages(value: unknown): ImageAttachment[] | undefined {
  if (!Array.isArray(value) || value.length > MAX_IMAGES) return undefined;
  const out: ImageAttachment[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) return undefined;
    if (!isAllowedImageMime(entry.mimeType)) return undefined;
    if (
      typeof entry.data !== "string" ||
      entry.data.length === 0 ||
      entry.data.length > MAX_IMAGE_DATA_CHARS ||
      !BASE64_PATTERN.test(entry.data)
    ) {
      return undefined;
    }
    if (entry.name !== undefined) {
      if (!shortString(entry.name, MAX_IMAGE_NAME_CHARS)) return undefined;
      out.push({ mimeType: entry.mimeType, data: entry.data, name: entry.name });
    } else {
      out.push({ mimeType: entry.mimeType, data: entry.data });
    }
  }
  return out;
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
    case "saveAll":
      return { type: value.type };
    case "send": {
      if (typeof value.text !== "string" || value.text.length > MAX_PROMPT_CHARS) return undefined;
      let mentions: MentionAttachment[] | undefined;
      if (value.mentions !== undefined) {
        mentions = parseMentions(value.mentions);
        if (!mentions) return undefined;
      }
      let images: ImageAttachment[] | undefined;
      if (value.images !== undefined) {
        images = parseImages(value.images);
        if (!images) return undefined;
      }
      const hasImages = images !== undefined && images.length > 0;
      if (value.text.trim() === "" && !hasImages) return undefined;
      return {
        type: "send",
        text: value.text,
        ...(mentions && mentions.length > 0 ? { mentions } : {}),
        ...(hasImages ? { images } : {}),
      };
    }
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
        value.value !== "xhigh" &&
        value.value !== "max"
      ) {
        return undefined;
      }
      return { type: "setThinking", value: value.value };
    case "setModel":
      if (!shortString(value.value, MAX_ID_CHARS)) return undefined;
      return { type: "setModel", value: value.value };
    case "queryMentions":
      if (
        typeof value.requestId !== "number" ||
        !Number.isSafeInteger(value.requestId) ||
        value.requestId < 0 ||
        typeof value.query !== "string" ||
        value.query.length > MAX_MENTION_QUERY_CHARS
      ) {
        return undefined;
      }
      return { type: "queryMentions", requestId: value.requestId, query: value.query };
    case "openFile": {
      if (typeof value.path !== "string") return undefined;
      const normalized = normalizeRelativePath(value.path, "file");
      if (!normalized) return undefined;
      if (value.line !== undefined) {
        if (typeof value.line !== "number" || !Number.isSafeInteger(value.line) || value.line < 1) {
          return undefined;
        }
        return { type: "openFile", path: normalized, line: value.line };
      }
      return { type: "openFile", path: normalized };
    }
    case "copy":
      if (!shortString(value.text, MAX_PROMPT_CHARS)) return undefined;
      return { type: "copy", text: value.text };
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
