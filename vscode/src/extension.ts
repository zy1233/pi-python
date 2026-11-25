import { existsSync, statSync } from "node:fs";
import path from "node:path";

import * as vscode from "vscode";

import { AVATAR_PRESETS, matchModelAvatarPreset, normalizeAvatarPreset } from "./core/avatar-presets.js";
import { ChatController } from "./core/chat-controller.js";
import { agentConfigPath } from "./core/first-run.js";
import { normalizeRelativePath } from "./core/mentions.js";
import type { SessionRow } from "./core/session-list.js";
import { CHAT_VIEW_ID, ChatViewProvider } from "./ui/chat-view.js";
import {
  AVATAR_IMAGE_EXTENSIONS,
  AVATAR_SETTING_KEY,
  AvatarProvider,
  cleanupStoredAvatars,
  importLocalAvatarFile,
  type AvatarRole,
} from "./ui/avatars.js";
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
  context.subscriptions.push(host, channel, status);

  const diffs = new DiffDocuments(() => chat.diffStore);
  const avatars = new AvatarProvider(context.extensionUri, host.log);
  const storageDir = context.globalStorageUri?.fsPath;
  let latestModelIds: [string | undefined, string | undefined] = [undefined, undefined];

  const promptDisableAutoModelIfActive = async (): Promise<void> => {
    const config = vscode.workspace.getConfiguration("zycode");
    if (config.get<boolean>("autoModelAvatar", true) && matchModelAvatarPreset(...latestModelIds) !== undefined) {
      const choice = await vscode.window.showInformationMessage(
        "zycode: Auto-switch avatar by model is on, so the active model's mascot is currently shown.",
        "Turn off auto-switch",
      );
      if (choice === "Turn off auto-switch") {
        await config.update("autoModelAvatar", false, vscode.ConfigurationTarget.Global);
        avatars.clearCustomCache();
        view.refreshAvatars();
      }
    }
  };

  const chooseAvatar = async (role: AvatarRole): Promise<void> => {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: false,
      openLabel: role === "user" ? "Select user avatar" : "Select zycode avatar",
      filters: { Images: [...AVATAR_IMAGE_EXTENSIONS] },
    });
    const uri = picked?.[0];
    if (!uri) return;
    const imported = await importLocalAvatarFile(uri.fsPath, role, storageDir);
    if (!imported.ok) {
      host.notify("warning", `zycode: ${imported.reason}`);
      return;
    }
    await vscode.workspace.getConfiguration("zycode").update(AVATAR_SETTING_KEY[role], imported.path, vscode.ConfigurationTarget.Global);
    avatars.clearCustomCache();
    view.refreshAvatars();
    if (role === "assistant") {
      void promptDisableAutoModelIfActive();
    }
  };

  const chooseAssistantPreset = async (): Promise<void> => {
    const config = vscode.workspace.getConfiguration("zycode");
    const currentPreset = normalizeAvatarPreset(config.get<string>("assistantAvatarPreset", "claude"));
    const items = AVATAR_PRESETS.map((preset) => ({
      preset: preset.id,
      label: preset.label,
      description: preset.id === currentPreset ? `${preset.description} (current)` : preset.description,
    }));
    const picked = await vscode.window.showQuickPick(items, {
      placeHolder: "Choose a built-in zycode mascot avatar",
    });
    if (!picked) return;
    await config.update("assistantAvatarPreset", picked.preset, vscode.ConfigurationTarget.Global);
    if (config.get<string>("assistantAvatar", "").trim() !== "") {
      await config.update("assistantAvatar", "", vscode.ConfigurationTarget.Global);
      await cleanupStoredAvatars("assistant", storageDir);
    }
    avatars.clearCustomCache();
    view.refreshAvatars();
    void promptDisableAutoModelIfActive();
  };

  const resetAvatar = async (role: AvatarRole): Promise<void> => {
    await vscode.workspace.getConfiguration("zycode").update(AVATAR_SETTING_KEY[role], "", vscode.ConfigurationTarget.Global);
    await cleanupStoredAvatars(role, storageDir);
    avatars.clearCustomCache();
    view.refreshAvatars();
  };

  const pickAvatar = async (role: AvatarRole): Promise<void> => {
    const config = vscode.workspace.getConfiguration("zycode");
    const key = AVATAR_SETTING_KEY[role];
    const current = config.get<string>(key, "").trim();
    if (role === "assistant") {
      const autoModel = config.get<boolean>("autoModelAvatar", true);
      const items: { id: "auto" | "preset" | "choose" | "reset" | "settings"; label: string; description: string }[] = [
        {
          id: "auto",
          label: `$(sparkle) Auto-switch by model: ${autoModel ? "On" : "Off"}`,
          description: autoModel ? "Click to turn off and always use your chosen avatar" : "Click to turn on automatic model mascot switching",
        },
        {
          id: "preset",
          label: "$(smiley) Choose built-in mascot…",
          description: "Claude, Gemini, DeepSeek, OpenAI, Grok, Kimi, Qwen",
        },
        {
          id: "choose",
          label: "$(file-media) Choose local image…",
          description: "Import an image file from your computer",
        },
      ];
      if (current !== "") {
        items.push({
          id: "reset",
          label: "$(discard) Reset custom image",
          description: "Use the selected built-in mascot",
        });
      }
      items.push({
        id: "settings",
        label: "$(gear) Open avatar settings",
        description: "Edit auto-switch, built-in mascot, or custom image in Settings",
      });
      const choice = await vscode.window.showQuickPick(items, {
        placeHolder: "Change zycode chat avatar",
      });
      if (!choice) return;
      if (choice.id === "auto") {
        await config.update("autoModelAvatar", !autoModel, vscode.ConfigurationTarget.Global);
        avatars.clearCustomCache();
        view.refreshAvatars();
      } else if (choice.id === "preset") {
        await chooseAssistantPreset();
      } else if (choice.id === "choose") {
        await chooseAvatar("assistant");
      } else if (choice.id === "reset") {
        await resetAvatar("assistant");
      } else {
        await vscode.commands.executeCommand("workbench.action.openSettings", "zycode.assistantAvatar");
      }
      return;
    }

    const items: { id: "choose" | "reset" | "settings"; label: string; description: string }[] = [
      { id: "choose", label: "$(file-media) Choose local image…", description: "Import an image file from your computer" },
    ];
    if (current !== "") {
      items.push({
        id: "reset",
        label: "$(discard) Reset to default",
        description: "Use your VS Code account avatar or default icon",
      });
    }
    items.push({
      id: "settings",
      label: "$(gear) Open avatar setting",
      description: "Edit file path, HTTPS URL, or data URI in Settings",
    });
    const choice = await vscode.window.showQuickPick(items, {
      placeHolder: "Change your chat avatar (You)",
    });
    if (!choice) return;
    if (choice.id === "choose") await chooseAvatar("user");
    else if (choice.id === "reset") await resetAvatar("user");
    else await vscode.commands.executeCommand("workbench.action.openSettings", "zycode.userAvatar");
  };

  const view = new ChatViewProvider(context.extensionUri, chat, {
    showLog: () => host.showLog(),
    showDiff: (info) => diffs.open(info),
    notify: (text) => host.notify("info", text),
    openGuide,
    openAgentConfig: () => void openAgentConfig(chat),
    resolveAvatars: (webview) => avatars.resolve(webview),
    pickAvatar,
  });
  const sessions = new SessionsView(chat);
  context.subscriptions.push(
    avatars,
    view,
    sessions,
    diffs,
    vscode.window.registerWebviewViewProvider(CHAT_VIEW_ID, view, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.workspace.registerTextDocumentContentProvider(DIFF_SCHEME, diffs),
  );

  context.subscriptions.push(
    chat.onMessage((message) => {
      if (message.type !== "state") return;
      status.update(message);
      latestModelIds = [message.models?.current, message.model];
      if (avatars.setActiveModel(message.models?.current, message.model)) {
        view.refreshAvatars();
      }
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
    vscode.commands.registerCommand("zycode.openSettings", () =>
      vscode.commands.executeCommand("workbench.action.openSettings", "@ext:zycode.zycode"),
    ),
    vscode.commands.registerCommand("zycode.openAgentConfig", () => openAgentConfig(chat)),
    vscode.commands.registerCommand("zycode.chooseUserAvatar", () => chooseAvatar("user")),
    vscode.commands.registerCommand("zycode.chooseAssistantAvatar", () => chooseAvatar("assistant")),
    vscode.commands.registerCommand("zycode.resetUserAvatar", () => resetAvatar("user")),
    vscode.commands.registerCommand("zycode.resetAssistantAvatar", () => resetAvatar("assistant")),
    vscode.commands.registerCommand("zycode.chooseAssistantPreset", chooseAssistantPreset),
    vscode.commands.registerCommand("zycode.refreshSessions", () => chat.refreshSessions()),
    vscode.commands.registerCommand("zycode.openSession", async (id: unknown) => {
      if (typeof id !== "string") return;
      await view.reveal();
      await chat.openPastSession(id);
    }),
    vscode.commands.registerCommand("zycode.deleteSession", async (row: SessionRow | undefined) => {
      if (row && typeof row.id === "string") await chat.deleteSession(row.id);
    }),
    vscode.commands.registerCommand("zycode.addSelectionToChat", async () => {
      const editor = vscode.window.activeTextEditor;
      const folder = chat.currentCwd ?? host.sessionFolder();
      if (!editor || editor.document.uri.scheme !== "file" || !folder) return;
      const rel = path.relative(folder, editor.document.uri.fsPath);
      const isSelection = !editor.selection.isEmpty;
      const normalized = normalizeRelativePath(rel, isSelection ? "selection" : "file");
      if (!normalized) {
        host.notify("info", "zycode: this file is outside the current chat folder.");
        return;
      }
      if (isSelection) {
        const startLine = editor.selection.start.line + 1;
        const endLine =
          editor.selection.end.character === 0 && editor.selection.end.line > editor.selection.start.line
            ? editor.selection.end.line
            : editor.selection.end.line + 1;
        await view.insertMention({ kind: "selection", path: normalized, range: { startLine, endLine } });
      } else {
        await view.insertMention({ kind: "file", path: normalized });
      }
    }),
    vscode.commands.registerCommand("zycode.addFileToChat", async (uri?: vscode.Uri) => {
      const targetUri = uri ?? vscode.window.activeTextEditor?.document.uri;
      const folder = chat.currentCwd ?? host.sessionFolder();
      if (!targetUri || targetUri.scheme !== "file" || !folder) return;
      let isDir = false;
      try {
        isDir = statSync(targetUri.fsPath).isDirectory();
      } catch {
        isDir = false;
      }
      const kind = isDir ? "folder" : "file";
      const normalized = normalizeRelativePath(path.relative(folder, targetUri.fsPath), kind);
      if (!normalized) {
        host.notify("info", "zycode: this file is outside the current chat folder.");
        return;
      }
      await view.insertMention({ kind, path: normalized });
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      if (chat.currentStatus === "untrusted") void chat.start();
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      if (chat.currentStatus === "no-folder") void chat.start();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("zycode.permissionMode")) {
        void chat.applyPermissionMode(host.settings().permissionMode);
      }
      if (event.affectsConfiguration("zycode.showThinking")) chat.refreshSettings();
      if (
        event.affectsConfiguration("zycode.userAvatar") ||
        event.affectsConfiguration("zycode.assistantAvatar") ||
        event.affectsConfiguration("zycode.assistantAvatarPreset") ||
        event.affectsConfiguration("zycode.autoModelAvatar")
      ) {
        avatars.clearCustomCache();
        view.refreshAvatars();
      }
      if (event.affectsConfiguration("zycode.agentPath") || event.affectsConfiguration("zycode.agentArgs")) {
        void vscode.window
          .showInformationMessage("zycode: the agent program changed. Restart the agent to use it.", "Restart agent")
          .then((choice) => (choice ? chat.restart(true) : undefined));
      }
    }),
  );

  if (vscode.authentication?.onDidChangeSessions) {
    context.subscriptions.push(
      vscode.authentication.onDidChangeSessions(() => {
        avatars.clearAccountCache();
        view.refreshAvatars();
      }),
    );
  }

  void chat.start();
}

export async function deactivate(): Promise<void> {
  const chat = controller;
  controller = undefined;
  await chat?.dispose();
}
