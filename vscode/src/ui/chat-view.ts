import * as vscode from "vscode";

import type { ChatController } from "../core/chat-controller.js";
import type { DiffInfo } from "../core/diff-tracker.js";
import { MAX_MENTIONS, type MentionAttachment } from "../core/mentions.js";
import {
  isSafeLink,
  parseWebviewMessage,
  type AvatarRole,
  type AvatarState,
  type HostMessage,
  type WebviewMessage,
} from "../core/protocol.js";
import { PermissionFallback } from "./permission-fallback.js";

export const CHAT_VIEW_ID = "zycode.chat";

function nonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * The webview of the chat (docs/specs/2026-11-04-vscode-extension-design.md, sections 2 and 6). It
 * forwards the controller's messages to the page, and the page's messages to the controller, after
 * `parseWebviewMessage` has checked each one.
 */
export class ChatViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private readonly subscription: { dispose(): void };
  private readonly fallback = new PermissionFallback();
  private latestMentionRequest = 0;
  private webviewReady = false;
  private pendingMentions: MentionAttachment[] = [];
  private avatarSeq = 0;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly controller: ChatController,
    private readonly actions: {
      showLog(): void;
      showDiff(info: DiffInfo): Promise<void>;
      notify(text: string): void;
      openGuide(): void;
      openAgentConfig(): void;
      resolveAvatars?: (webview: vscode.Webview) => Promise<AvatarState>;
      pickAvatar?: (role: AvatarRole) => Promise<void> | void;
    },
  ) {
    this.subscription = controller.onMessage((message) => {
      this.post(message);
      if (message.type === "permission" && !this.view?.visible) {
        this.fallback.show(message, {
          answer: (requestId, optionId) => this.controller.answerPermission(requestId, optionId),
          reveal: () => this.reveal(),
        });
      } else if (message.type === "permissionDone") {
        this.fallback.forget(message.requestId);
      }
    });
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.webviewReady = false;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist"), vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((raw: unknown) => {
      const message = parseWebviewMessage(raw);
      if (message) void this.handle(message);
    });
    view.onDidDispose(() => {
      if (this.view === view) {
        this.view = undefined;
        this.webviewReady = false;
        this.latestMentionRequest = 0;
      }
    });
  }

  /** Brings the view to the front. Resolves the view first if it was never shown. */
  async reveal(): Promise<void> {
    if (this.view) this.view.show(false);
    else await vscode.commands.executeCommand(`${CHAT_VIEW_ID}.focus`);
  }

  /** Reveals the chat view and inserts a `@` mention attachment into the composer. */
  async insertMention(item: MentionAttachment): Promise<void> {
    await this.reveal();
    if (this.webviewReady) {
      this.post({ type: "insertMention", item });
    } else if (this.pendingMentions.length < MAX_MENTIONS) {
      this.pendingMentions.push(item);
    }
  }

  dispose(): void {
    this.webviewReady = false;
    this.pendingMentions.length = 0;
    this.fallback.clear();
    this.subscription.dispose();
  }

  /** Re-resolves and posts user and assistant avatars to the active webview, if ready. */
  refreshAvatars(): void {
    if (!this.view || !this.webviewReady || !this.actions.resolveAvatars) return;
    const seq = ++this.avatarSeq;
    const webview = this.view.webview;
    void this.actions
      .resolveAvatars(webview)
      .then((avatars) => {
        if (this.webviewReady && this.view?.webview === webview && seq === this.avatarSeq) {
          this.post({ type: "avatars", avatars });
        }
      })
      .catch(() => undefined);
  }

  private post(message: HostMessage): void {
    void this.view?.webview.postMessage(message);
  }

  private async handle(message: WebviewMessage): Promise<void> {
    const controller = this.controller;
    switch (message.type) {
      case "ready":
        this.webviewReady = true;
        this.latestMentionRequest = 0;
        this.post(controller.stateMessage());
        this.post({ type: "reset", transcript: controller.transcript() });
        for (const item of this.pendingMentions.splice(0)) {
          this.post({ type: "insertMention", item });
        }
        this.refreshAvatars();
        if (controller.currentStatus === "stopped") await controller.start();
        return;
      case "send":
        await controller.send(message.text, message.mentions, message.images);
        return;
      case "cancel":
        await controller.cancel();
        return;
      case "newChat":
        await controller.newChat();
        return;
      case "permissionAnswer":
        controller.answerPermission(message.requestId, message.optionId);
        return;
      case "setMode":
        await controller.applyPermissionMode(message.value);
        return;
      case "setThinking":
        await controller.applyThinkingLevel(message.value);
        return;
      case "setModel":
        await controller.applyModel(message.value);
        return;
      case "queryMentions": {
        if (message.requestId < this.latestMentionRequest) return;
        this.latestMentionRequest = message.requestId;
        const items = await controller.searchMentions(message.query);
        if (message.requestId !== this.latestMentionRequest) return;
        this.post({ type: "mentionResults", requestId: message.requestId, query: message.query, items });
        return;
      }
      case "runSetup":
        await controller.runSetup();
        return;
      case "openGuide":
        this.actions.openGuide();
        return;
      case "openAgentConfig":
        this.actions.openAgentConfig();
        return;
      case "restartAgent":
        await controller.restart(true);
        return;
      case "openLog":
        this.actions.showLog();
        return;
      case "saveAll":
        await controller.saveAll();
        return;
      case "openFile":
        await controller.openFile(message.path, message.line);
        return;
      case "copy":
        await vscode.env.clipboard.writeText(message.text);
        return;
      case "openLink":
        if (isSafeLink(message.url)) await vscode.env.openExternal(vscode.Uri.parse(message.url, true));
        return;
      case "pickAvatar":
        await this.actions.pickAvatar?.(message.role);
        return;
      case "showDiff": {
        const info = controller.diffFor(message.toolCallId);
        if (info) await this.actions.showDiff(info);
        else this.actions.notify("zycode: no diff is available for this call.");
        return;
      }
    }
  }

  private html(webview: vscode.Webview): string {
    const script = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "dist", "webview.js"));
    const style = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "chat.css"));
    const token = nonce();
    const csp = [
      "default-src 'none'",
      `style-src ${webview.cspSource}`,
      `script-src 'nonce-${token}'`,
      `img-src ${webview.cspSource} data:`,
    ].join("; ");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${style.toString()}">
<title>zycode</title>
</head>
<body>
<div id="root"></div>
<script nonce="${token}" src="${script.toString()}"></script>
</body>
</html>`;
  }
}
