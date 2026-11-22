import os from "node:os";
import path from "node:path";

import * as vscode from "vscode";

import { agentConfigPath } from "../core/first-run.js";
import { parseMcpServers, type ConfirmRequest, type Host, type Logger, type Settings, type SetupTerminalRequest } from "../core/host.js";
import { normalizeRelativePath, type RankedMention } from "../core/mentions.js";
import type { StartModeName } from "../core/protocol.js";
import { WorkspaceMentions } from "./workspace-mentions.js";

export interface HostInfo {
  extensionPath: string;
  extensionVersion: string;
  agentVersionPin: string;
}

class ChannelLogger implements Logger {
  constructor(private readonly channel: vscode.LogOutputChannel) {}
  info(message: string): void {
    this.channel.info(message);
  }
  warn(message: string): void {
    this.channel.warn(message);
  }
  error(message: string): void {
    this.channel.error(message);
  }
}

const MAX_DIFF_FILE_BYTES = 2 * 1024 * 1024;

function underFolder(file: string, folder: string): boolean {
  const relative = path.relative(folder, file);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

/** The `Host` of the core, made of the `vscode` module. */
export class VsCodeHost implements Host, vscode.Disposable {
  readonly log: Logger;
  readonly extensionPath: string;
  readonly extensionVersion: string;
  readonly agentVersionPin: string;
  readonly platform = process.platform;
  private readonly mentions: WorkspaceMentions;

  constructor(
    private readonly channel: vscode.LogOutputChannel,
    info: HostInfo,
  ) {
    this.log = new ChannelLogger(channel);
    this.mentions = new WorkspaceMentions(this.log);
    this.extensionPath = info.extensionPath;
    this.extensionVersion = info.extensionVersion;
    this.agentVersionPin = info.agentVersionPin;
  }

  settings(): Settings {
    const config = vscode.workspace.getConfiguration("zycode");
    const mode = config.get<string>("permissionMode", "ask");
    const permissionMode: StartModeName = mode === "auto" ? "auto" : "ask";
    return {
      agentPath: config.get<string>("agentPath", ""),
      agentArgs: config.get<string[]>("agentArgs", []).filter((arg) => typeof arg === "string"),
      permissionMode,
      showThinking: config.get<boolean>("showThinking", true),
      mcpServers: parseMcpServers(config.get<unknown>("mcpServers", [])),
    };
  }

  isTrusted(): boolean {
    return vscode.workspace.isTrusted;
  }

  agentConfigPath(): string {
    return agentConfigPath();
  }

  sessionFolder(): string | undefined {
    const activeUri = vscode.window.activeTextEditor?.document.uri;
    if (activeUri && activeUri.scheme === "file") {
      const activeFolder = vscode.workspace.getWorkspaceFolder?.(activeUri);
      if (activeFolder && activeFolder.uri.scheme === "file") {
        return activeFolder.uri.fsPath;
      }
    }
    return vscode.workspace.workspaceFolders?.find((folder) => folder.uri.scheme === "file")?.uri.fsPath;
  }

  dirtyFiles(folder: string): string[] {
    return vscode.workspace.textDocuments
      .filter((doc) => doc.isDirty && doc.uri.scheme === "file" && underFolder(doc.uri.fsPath, folder))
      .map((doc) => doc.uri.fsPath);
  }

  async readFile(file: string): Promise<string | null | undefined> {
    try {
      const bytes = await vscode.workspace.fs.readFile(vscode.Uri.file(file));
      if (bytes.byteLength > MAX_DIFF_FILE_BYTES) return undefined;
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (error) {
      if (error instanceof vscode.FileSystemError && error.code === "FileNotFound") return null;
      return undefined;
    }
  }

  runSetupTerminal(request: SetupTerminalRequest): Promise<{ exitCode: number | undefined }> {
    const terminal = vscode.window.createTerminal({
      name: "zycode setup",
      shellPath: request.command,
      shellArgs: request.args,
      env: request.env,
      cwd: request.cwd ?? os.homedir(),
    });
    terminal.show();
    return new Promise((resolve) => {
      const subscription = vscode.window.onDidCloseTerminal((closed) => {
        if (closed !== terminal) return;
        subscription.dispose();
        resolve({ exitCode: closed.exitStatus?.code });
      });
    });
  }

  async confirm(request: ConfirmRequest): Promise<boolean> {
    const choice = await vscode.window.showWarningMessage(request.message, { modal: true, detail: request.detail }, request.action);
    return choice === request.action;
  }

  notify(level: "info" | "warning" | "error", text: string): void {
    const show =
      level === "error"
        ? vscode.window.showErrorMessage
        : level === "warning"
          ? vscode.window.showWarningMessage
          : vscode.window.showInformationMessage;
    void show(text);
  }

  showLog(): void {
    this.channel.show(true);
  }

  searchMentions(folder: string, query: string, limit?: number): Promise<RankedMention[]> {
    return this.mentions.search(folder, query, limit);
  }

  async saveAll(): Promise<boolean> {
    return vscode.workspace.saveAll(false);
  }

  async openFile(folder: string, relPath: string, line?: number): Promise<void> {
    const normalized = normalizeRelativePath(relPath, "file");
    if (!normalized) return;
    const target = path.resolve(folder, normalized);
    if (!underFolder(target, folder)) return;
    const zeroLine = line !== undefined && line >= 1 ? line - 1 : undefined;
    const options =
      zeroLine !== undefined
        ? { selection: new vscode.Range(zeroLine, 0, zeroLine, 0) }
        : undefined;
    await vscode.window.showTextDocument(vscode.Uri.file(target), options);
  }

  dispose(): void {
    this.mentions.dispose();
  }
}
