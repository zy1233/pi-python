import * as vscode from "vscode";

import { ChatController } from "./core/chat-controller.js";
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
  const status = new StatusBar("zycode.showLog");
  context.subscriptions.push(channel, status);

  context.subscriptions.push(
    chat.onMessage((message) => {
      if (message.type === "state") status.update(message);
    }),
    vscode.commands.registerCommand("zycode.restartAgent", () => chat.restart()),
    vscode.commands.registerCommand("zycode.showLog", () => host.showLog()),
    vscode.commands.registerCommand("zycode.runSetup", () => chat.runSetup()),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      if (chat.currentStatus === "untrusted") void chat.start();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("zycode.permissionMode")) {
        void chat.applyPermissionMode(host.settings().permissionMode);
      }
      if (event.affectsConfiguration("zycode.agentPath") || event.affectsConfiguration("zycode.agentArgs")) {
        void vscode.window
          .showInformationMessage("zycode: the agent program changed. Restart the agent to use it.", "Restart agent")
          .then((choice) => (choice ? chat.restart() : undefined));
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
