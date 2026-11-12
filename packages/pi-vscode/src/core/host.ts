/**
 * What the core needs from VS Code. `src/ui` implements it with the `vscode` module, and the
 * tests implement it with plain objects (docs/specs/2026-11-04-vscode-extension-design.md, section 2).
 */

import type { PermissionModeName } from "./protocol.js";

export interface Logger {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
}

export interface Settings {
  agentPath: string;
  agentArgs: string[];
  permissionMode: PermissionModeName;
  showThinking: boolean;
}

export interface SetupTerminalRequest {
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
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
  /** Shows a notification that has no chat to go to (for example a version warning). */
  notify(level: "info" | "warning" | "error", text: string): void;
}
