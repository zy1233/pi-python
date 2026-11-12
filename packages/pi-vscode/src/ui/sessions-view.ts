import * as vscode from "vscode";

import type { ChatController } from "../core/chat-controller.js";
import { displayTitle, listMessage, timeAgo, type SessionRow } from "../core/session-list.js";

export const SESSIONS_VIEW_ID = "zycode.sessions";
const REPAINT_MS = 60_000;

/**
 * The panel "Past chats" (docs/specs/2026-11-04-vscode-extension-design.md, section 7b): a tree view
 * under the chat. One row for each past session of the open folder, in the order of the agent
 * (newest first). A click opens it, the trash button deletes it. The empty state and the error
 * state are the `message` of the view.
 */
export class SessionsView implements vscode.TreeDataProvider<SessionRow>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<SessionRow | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly view: vscode.TreeView<SessionRow>;
  private readonly subscription: { dispose(): void };
  private readonly timer: NodeJS.Timeout;

  constructor(private readonly controller: ChatController) {
    this.view = vscode.window.createTreeView(SESSIONS_VIEW_ID, { treeDataProvider: this });
    this.subscription = controller.onSessionsChanged(() => this.update());
    // The text "5m ago" goes stale while nothing changes.
    this.timer = setInterval(() => this.changed.fire(undefined), REPAINT_MS);
    this.update();
  }

  private update(): void {
    this.view.message = listMessage(this.controller.sessions);
    this.changed.fire(undefined);
  }

  getChildren(element?: SessionRow): SessionRow[] {
    if (element) return [];
    const state = this.controller.sessions;
    return state.kind === "ready" || state.kind === "loading" ? state.rows : [];
  }

  getTreeItem(row: SessionRow): vscode.TreeItem {
    const current = row.id === this.controller.currentSessionId;
    const item = new vscode.TreeItem(displayTitle(row), vscode.TreeItemCollapsibleState.None);
    item.id = row.id;
    item.description = current ? `open · ${timeAgo(row.updatedAt, Date.now())}` : timeAgo(row.updatedAt, Date.now());
    item.iconPath = new vscode.ThemeIcon(current ? "comment-discussion" : "comment");
    const when = row.updatedAt === undefined ? "" : `\nLast message: ${new Date(row.updatedAt).toLocaleString()}`;
    item.tooltip = `${row.title.trim() === "" ? displayTitle(row) : row.title.trim()}${when}\nFolder: ${row.cwd}\nId: ${row.id}`;
    item.contextValue = this.controller.canDeleteSessions ? "zycode.session" : "zycode.session.nodelete";
    item.command = { command: "zycode.openSession", title: "Open chat", arguments: [row.id] };
    return item;
  }

  dispose(): void {
    clearInterval(this.timer);
    this.subscription.dispose();
    this.view.dispose();
    this.changed.dispose();
  }
}
