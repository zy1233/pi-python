import { existsSync } from "node:fs";

import * as vscode from "vscode";

import { ChatController } from "./core/chat-controller.js";
import { agentConfigPath } from "./core/first-run.js";
import type { SessionRow } from "./core/session-list.js";
import { CHAT_VIEW_ID, ChatViewProvider } from "./ui/chat-view.js";
import { DIFF_SCHEME, DiffDocuments } from "./ui/diff-documents.js";
import { SessionsView } from "./ui/sessions-view.js";
import { StatusBar } from "./ui/status-bar.js";
import { VsCodeHost } from "./ui/vscode-host.js";

let controller: ChatController | undefined;

const WALKTHROUGH_ID = "zycode.zycode#zycode.gettingStarted";
const GUIDE_SHOWN_KEY = "zycode.guideShown";

function openGuide(): void {
  void vscode.commands.executeCommand("workbench.action.openWalkthrough", WALKTHROUGH_ID, false);
}

/** `agent.toml` holds the model and no key (the key is in `local.env`), so it is safe to open. */
async function openAgentConfig(chat: ChatController): Promise<void> {
  const file = agentConfigPath();
  if (!existsSync(file)) {
    const choice = await vscode.window.showInformationMessage(
      "zycode: agent.toml does not exist yet. The setup creates it.",
      "Run setup",
    );
    if (choice) await chat.runSetup();
    return;
  }
  await vscode.window.showTextDocument(vscode.Uri.file(file));
}

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
    openGuide,
    openAgentConfig: () => void openAgentConfig(chat),
  });
  const sessions = new SessionsView(chat);
  context.subscriptions.push(
    view,
    sessions,
    diffs,
    vscode.window.registerWebviewViewProvider(CHAT_VIEW_ID, view),
    vscode.workspace.registerTextDocumentContentProvider(DIFF_SCHEME, diffs),
  );

  context.subscriptions.push(
    chat.onMessage((message) => {
      if (message.type !== "state") return;
      status.update(message);
      // The guide opens by itself once, when the first start finds that nothing is set up.
      if (message.status === "needs-setup" && context.globalState.get<boolean>(GUIDE_SHOWN_KEY) !== true) {
        void context.globalState.update(GUIDE_SHOWN_KEY, true);
        openGuide();
      }
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
    vscode.commands.registerCommand("zycode.openGuide", openGuide),
    vscode.commands.registerCommand("zycode.openAgentConfig", () => openAgentConfig(chat)),
    vscode.commands.registerCommand("zycode.refreshSessions", () => chat.refreshSessions()),
    vscode.commands.registerCommand("zycode.openSession", async (id: unknown) => {
      if (typeof id !== "string") return;
      await view.reveal();
      await chat.openPastSession(id);
    }),
    vscode.commands.registerCommand("zycode.deleteSession", async (row: SessionRow | undefined) => {
      if (row && typeof row.id === "string") await chat.deleteSession(row.id);
    }),
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
