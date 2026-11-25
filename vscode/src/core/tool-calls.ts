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

export interface ToolApplyResult {
  view: ToolCallView;
  terminalDelta?: string;
  pureTerminalAppend: boolean;
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

  /** Replaces the stored view of a call (the controller adds `canDiff`). Returns the view that is kept. */
  update(id: string, view: ToolCallView): ToolCallView {
    this.calls.set(id, view);
    return view;
  }

  /** Applies one update and returns the call as it is now, or `undefined` for an update with no id. */
  apply(update: RawToolUpdate): ToolCallView | undefined {
    return this.applyWithDelta(update)?.view;
  }

  /**
   * Applies one update and reports whether it was a pure incremental terminal output append so the
   * host can emit a small delta instead of resending the full accumulated output over IPC.
   */
  applyWithDelta(update: RawToolUpdate): ToolApplyResult | undefined {
    if (typeof update.toolCallId !== "string" || update.toolCallId === "") return undefined;
    const id = update.toolCallId;
    const previous = this.calls.get(id);
    const view: ToolCallView = previous
      ? {
          ...previous,
          ...(previous.terminal ? { terminal: { ...previous.terminal } } : {}),
        }
      : { id, title: typeof update.title === "string" ? update.title : id, kind: "other", status: "pending" };
    if (typeof update.title === "string" && update.title !== "") view.title = update.title;
    if (typeof update.kind === "string") view.kind = update.kind;
    if (isStatus(update.status)) view.status = update.status;
    if (update.rawInput !== undefined) view.rawInput = update.rawInput;
    let terminalDelta: string | undefined;
    const meta = isRecord(update._meta) ? update._meta : undefined;
    if (meta) {
      const output = isRecord(meta.terminal_output) ? meta.terminal_output : undefined;
      if (output && typeof output.data === "string") {
        terminalDelta = output.data;
        view.terminal = { ...view.terminal, output: (view.terminal?.output ?? "") + output.data };
      }
      const exit = isRecord(meta.terminal_exit) ? meta.terminal_exit : undefined;
      if (exit && typeof exit.exit_code === "number") {
        view.terminal = { output: view.terminal?.output ?? "", ...view.terminal, exitCode: exit.exit_code };
      }
    }
    const text = lastTextBlock(update.content);
    // Avoid duplicating full terminal stdout in both `view.terminal.output` and `view.text`.
    if (view.terminal !== undefined && (view.terminal.output !== "" || text === undefined || text === "")) {
      delete view.text;
    } else if (text !== undefined) {
      view.text = text;
    }
    const pureTerminalAppend =
      previous !== undefined &&
      terminalDelta !== undefined &&
      terminalDelta !== "" &&
      view.status === "in_progress" &&
      previous.status === view.status &&
      previous.title === view.title &&
      previous.kind === view.kind &&
      previous.text === view.text &&
      previous.terminal?.exitCode === view.terminal?.exitCode &&
      update.rawInput === undefined;
    this.calls.set(id, view);
    return {
      view,
      ...(terminalDelta !== undefined ? { terminalDelta } : {}),
      pureTerminalAppend,
    };
  }
}

/** The name of the tool in a card title: the text up to the first white space or `(`. */
function toolNameOf(title: string): string {
  return /^[^\s(]*/.exec(title)![0];
}

/**
 * What the permission card shows under the title, whole and never cut. It shows what the agent runs:
 * the command of `bash`, the path of `read`, `write` and `edit`, the script of `codemode` (`code`),
 * and the script of `workflow`. For every tool, `command`, `path`, `code` and `script` are read in
 * that order. The one tool with more is `workflow`: the agent runs a built-in workflow when `name` is
 * a non-empty string and ignores `script` then, so for `workflow` (the title is `workflow`,
 * `workflow (codemode)` or `workflow (Auto: ...)`) the card shows the name, the whole `args` as
 * JSON, and a line that says that `script` is ignored. Any other tool has no `name` meaning here:
 * an MCP tool with `{ name: "my-repo" }` shows nothing, and so does a call with no `title`. The card
 * scrolls a long text (`.perm-summary` in `media/chat.css`), and the Allow and Reject buttons stay
 * outside the scroll area.
 */
export function summarizeInput(rawInput: unknown, title?: string): string {
  if (!isRecord(rawInput)) return "";
  for (const key of ["command", "path", "code"]) {
    const value = rawInput[key];
    if (typeof value === "string") return value;
  }
  if (title !== undefined && toolNameOf(title) === "workflow" && typeof rawInput.name === "string" && rawInput.name !== "") {
    const lines = [`built-in workflow: ${rawInput.name}`];
    if (rawInput.args !== undefined && rawInput.args !== null) {
      const args = JSON.stringify(rawInput.args);
      if (args !== undefined) lines.push(`args: ${args}`);
    }
    if (typeof rawInput.script === "string") lines.push("The agent ignores `script` when `name` is set.");
    return lines.join("\n");
  }
  if (typeof rawInput.script === "string") return rawInput.script;
  return "";
}
