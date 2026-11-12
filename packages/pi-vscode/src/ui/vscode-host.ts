import os from "node:os";
import path from "node:path";

import * as vscode from "vscode";

import type { ConfirmRequest, Host, Logger, Settings, SetupTerminalRequest } from "../core/host.js";
import type { StartModeName } from "../core/protocol.js";

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
export class VsCodeHost implements Host {
  readonly log: Logger;
  readonly extensionPath: string;
  readonly extensionVersion: string;
  readonly agentVersionPin: string;
  readonly platform = process.platform;

  constructor(
    private readonly channel: vscode.LogOutputChannel,
    info: HostInfo,
  ) {
    this.log = new ChannelLogger(channel);
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
    };
  }

  isTrusted(): boolean {
    return vscode.workspace.isTrusted;
  }

  sessionFolder(): string | undefined {
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
}
