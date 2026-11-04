import * as vscode from "vscode";

import { ChatController } from "./core/chat-controller.js";
import { CHAT_VIEW_ID, ChatViewProvider } from "./ui/chat-view.js";
import { DIFF_SCHEME, DiffDocuments } from "./ui/diff-documents.js";
import { StatusBar } from "./ui/status-bar.js";
import { VsCodeHost } from "./ui/vscode-host.js";

let controller: ChatController | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const pkg = context.extension.packageJSON as { version: string; zycodeAgentVersion: string };
  const channel = vscode.window.createOutputChannel("zycode", { log: true });
  const host = new VsCodeHost(channel, {
    extensionPath: context.extensionPath,
    extensionVersion: pkg.version,
    agentVersionPin: pkg.zycodeAgentVersion,
  });
  const chat = new ChatController(host);
  controller = chat;
  const status = new StatusBar("zycode.openChat");
  context.subscriptions.push(channel, status);

  const diffs = new DiffDocuments(() => chat.diffStore);
  const view = new ChatViewProvider(context.extensionUri, chat, {
    showLog: () => host.showLog(),
    showDiff: (info) => diffs.open(info),
    notify: (text) => host.notify("info", text),
  });
  context.subscriptions.push(
    view,
    diffs,
    vscode.window.registerWebviewViewProvider(CHAT_VIEW_ID, view),
    vscode.workspace.registerTextDocumentContentProvider(DIFF_SCHEME, diffs),
  );

  context.subscriptions.push(
    chat.onMessage((message) => {
      if (message.type === "state") status.update(message);
    }),
    vscode.commands.registerCommand("zycode.openChat", async () => {
      await view.reveal();
      if (chat.currentStatus === "stopped") await chat.start();
    }),
    vscode.commands.registerCommand("zycode.newChat", async () => {
      await view.reveal();
      await chat.newChat();
    }),
    vscode.commands.registerCommand("zycode.stop", () => chat.cancel()),
    vscode.commands.registerCommand("zycode.restartAgent", () => chat.restart(true)),
    vscode.commands.registerCommand("zycode.showLog", () => host.showLog()),
    vscode.commands.registerCommand("zycode.runSetup", () => chat.runSetup()),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      if (chat.currentStatus === "untrusted") void chat.start();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("zycode.permissionMode")) {
        void chat.applyPermissionMode(host.settings().permissionMode);
      }
      if (event.affectsConfiguration("zycode.showThinking")) chat.refreshSettings();
      if (event.affectsConfiguration("zycode.agentPath") || event.affectsConfiguration("zycode.agentArgs")) {
        void vscode.window
          .showInformationMessage("zycode: the agent program changed. Restart the agent to use it.", "Restart agent")
          .then((choice) => (choice ? chat.restart(true) : undefined));
      }
    }),
  );

  void chat.start();
}

export async function deactivate(): Promise<void> {
  const chat = controller;
  controller = undefined;
  await chat?.dispose();
}
