/**
 * What the core needs from VS Code. `src/ui` implements it with the `vscode` module, and the
 * tests implement it with plain objects (docs/specs/2026-11-04-vscode-extension-design.md, section 2).
 */

import type { RankedMention } from "./mentions.js";
import type { StartModeName } from "./protocol.js";

export interface Logger {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
}

export type McpServerConfig =
  | {
      name: string;
      command: string;
      args: string[];
      env: { name: string; value: string }[];
    }
  | {
      type: "http" | "sse";
      name: string;
      url: string;
      headers: { name: string; value: string }[];
    };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseNameValuePairs(raw: unknown): { name: string; value: string }[] | undefined {
  if (raw === undefined) return [];
  if (!Array.isArray(raw)) return undefined;
  const out: { name: string; value: string }[] = [];
  for (const item of raw) {
    if (!isRecord(item) || typeof item.name !== "string" || item.name.trim() === "" || typeof item.value !== "string") {
      return undefined;
    }
    out.push({ name: item.name.trim(), value: item.value });
  }
  return out;
}

/** Validates user-configured MCP server entries from `zycode.mcpServers`, skipping malformed items. */
export function parseMcpServers(raw: unknown): McpServerConfig[] {
  if (!Array.isArray(raw)) return [];
  const out: McpServerConfig[] = [];
  for (const entry of raw) {
    if (!isRecord(entry) || typeof entry.name !== "string" || entry.name.trim() === "") continue;
    const name = entry.name.trim();
    if (entry.type === "http" || entry.type === "sse") {
      if (typeof entry.url !== "string" || entry.url.trim() === "") continue;
      const headers = parseNameValuePairs(entry.headers);
      if (!headers) continue;
      out.push({ type: entry.type, name, url: entry.url.trim(), headers });
    } else if (entry.type === undefined || entry.type === "stdio") {
      if (typeof entry.command !== "string" || entry.command.trim() === "") continue;
      if (entry.args !== undefined && (!Array.isArray(entry.args) || !entry.args.every((a): a is string => typeof a === "string"))) {
        continue;
      }
      const env = parseNameValuePairs(entry.env);
      if (!env) continue;
      out.push({
        name,
        command: entry.command.trim(),
        args: entry.args ? [...entry.args] : [],
        env,
      });
    }
  }
  return out;
}

export interface Settings {
  agentPath: string;
  agentArgs: string[];
  /** The mode of a start. It is never `always-approve`: that is chosen in the chat and is not kept. */
  permissionMode: StartModeName;
  showThinking: boolean;
  mcpServers?: McpServerConfig[];
}

export interface SetupTerminalRequest {
  command: string;
  args: string[];
  env: Record<string, string>;
  /** Absent when no folder is open: the host picks the home folder of the user. */
  cwd?: string;
}

export interface ConfirmRequest {
  message: string;
  detail: string;
  /** The label of the button that confirms. The cancel button is always there. */
  action: string;
}

export interface Host {
  readonly log: Logger;
  /** The folder of the extension (where `bin/` is). */
  readonly extensionPath: string;
  readonly extensionVersion: string;
  /** `zycodeAgentVersion` of package.json. */
  readonly agentVersionPin: string;
  readonly platform: NodeJS.Platform;
  settings(): Settings;
  /** VS Code Workspace Trust. */
  isTrusted(): boolean;
  /** `agent.toml` of the agent (`PI_HOME`, else `~/.pi-python`). The thinking level is read from it and written to it. */
  agentConfigPath(): string;
  /** The folder a session works in, or `undefined` when no folder is open. */
  sessionFolder(): string | undefined;
  /** Paths of open documents under `folder` that have unsaved changes. */
  dirtyFiles(folder: string): string[];
  /**
   * The text of a file, `null` if it does not exist, `undefined` if it cannot be shown (too large,
   * not UTF-8, unreadable). The diff view reads files with it. `path` is absolute.
   */
  readFile(path: string): Promise<string | null | undefined>;
  /** Runs the setup program in a terminal and resolves when the terminal closes. */
  runSetupTerminal(request: SetupTerminalRequest): Promise<{ exitCode: number | undefined }>;
  /** A modal question. `true` only if the user pressed the confirm button. */
  confirm(request: ConfirmRequest): Promise<boolean>;
  /** Shows a notification that has no chat to go to (for example a version warning). */
  notify(level: "info" | "warning" | "error", text: string): void;
  /** Searches files, folders, and active editor context for `@` mentions in `folder`. */
  searchMentions?(folder: string, query: string, limit?: number): Promise<RankedMention[]>;
  /** Saves all dirty files in the workspace. */
  saveAll?(): Promise<boolean>;
  /** Opens a workspace-relative file in the editor, optionally positioning the cursor at `line` (1-based). */
  openFile?(folder: string, relPath: string, line?: number): Promise<void>;
}
