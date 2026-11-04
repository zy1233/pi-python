import type { ToolCallView, ToolStatus } from "./protocol.js";

interface RawToolUpdate {
  toolCallId?: unknown;
  title?: unknown;
  kind?: unknown;
  status?: unknown;
  rawInput?: unknown;
  content?: unknown;
  _meta?: unknown;
}

const STATUSES: readonly ToolStatus[] = ["pending", "in_progress", "completed", "failed"];

function isStatus(value: unknown): value is ToolStatus {
  return typeof value === "string" && (STATUSES as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The text of the last `content` block of an update that holds text, or `undefined`. */
export function lastTextBlock(content: unknown): string | undefined {
  if (!Array.isArray(content)) return undefined;
  let found: string | undefined;
  for (const item of content) {
    if (!isRecord(item) || item.type !== "content" || !isRecord(item.content)) continue;
    if (item.content.type === "text" && typeof item.content.text === "string") {
      found = item.content.text;
    }
  }
  return found;
}

/**
 * Merges `tool_call` and `tool_call_update` into one view for each call. The agent sends the
 * title and `rawInput` with the first update. Later updates carry the status and the content.
 * For `bash`, output arrives as deltas in `_meta.terminal_output` and the end carries the exit code.
 */
export class ToolCallStore {
  private readonly calls = new Map<string, ToolCallView>();

  get(id: string): ToolCallView | undefined {
    return this.calls.get(id);
  }

  clear(): void {
    this.calls.clear();
  }

  /** Applies one update and returns the call as it is now, or `undefined` for an update with no id. */
  apply(update: RawToolUpdate): ToolCallView | undefined {
    if (typeof update.toolCallId !== "string" || update.toolCallId === "") return undefined;
    const id = update.toolCallId;
    const previous = this.calls.get(id);
    const view: ToolCallView = previous
      ? { ...previous }
      : { id, title: typeof update.title === "string" ? update.title : id, kind: "other", status: "pending" };
    if (typeof update.title === "string" && update.title !== "") view.title = update.title;
    if (typeof update.kind === "string") view.kind = update.kind;
    if (isStatus(update.status)) view.status = update.status;
    if (update.rawInput !== undefined) view.rawInput = update.rawInput;
    const text = lastTextBlock(update.content);
    if (text !== undefined) view.text = text;
    const meta = isRecord(update._meta) ? update._meta : undefined;
    if (meta) {
      const output = isRecord(meta.terminal_output) ? meta.terminal_output : undefined;
      if (output && typeof output.data === "string") {
        view.terminal = { ...view.terminal, output: (view.terminal?.output ?? "") + output.data };
      }
      const exit = isRecord(meta.terminal_exit) ? meta.terminal_exit : undefined;
      if (exit && typeof exit.exit_code === "number") {
        view.terminal = { output: view.terminal?.output ?? "", ...view.terminal, exitCode: exit.exit_code };
      }
    }
    this.calls.set(id, view);
    return view;
  }
}

/** What the permission card shows under the title: the whole command, or the path. */
export function summarizeInput(rawInput: unknown): string {
  if (!isRecord(rawInput)) return "";
  if (typeof rawInput.command === "string") return rawInput.command;
  if (typeof rawInput.path === "string") return rawInput.path;
  return "";
}
