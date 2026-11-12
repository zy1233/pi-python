import * as vscode from "vscode";

import type { StateMessage, Status } from "../core/protocol.js";

const LABELS: Record<Status, string> = {
  stopped: "stopped",
  starting: "starting",
  ready: "ready",
  busy: "busy",
  cancelling: "stopping",
  "needs-setup": "setup",
  untrusted: "untrusted",
  "no-folder": "no folder",
  unavailable: "no agent",
  error: "error",
};

/** One item on the left of the status bar: `zycode: <state>`. */
export class StatusBar implements vscode.Disposable {
  private readonly item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 0);

  constructor(command: string) {
    this.item.name = "zycode";
    this.item.command = command;
    this.update({ type: "state", status: "stopped", canRestart: true });
    this.item.show();
  }

  update(state: StateMessage): void {
    const alwaysApprove = state.mode?.current === "always-approve";
    this.item.text =
      `$(${state.status === "busy" ? "sync~spin" : alwaysApprove ? "warning" : "sparkle"}) zycode: ${LABELS[state.status]}` +
      (alwaysApprove ? " · always approve" : "");
    const lines = [`zycode: ${LABELS[state.status]}`];
    if (state.detail) lines.push(state.detail);
    if (state.agent) lines.push(`Agent ${state.agent.version} (${state.agent.source})`);
    if (state.model) lines.push(`Model: ${state.model}`);
    if (state.mode) lines.push(`Mode: ${state.mode.current}`);
    if (alwaysApprove) lines.push("Every tool call runs with no question, bash too.");
    this.item.tooltip = lines.join("\n");
    this.item.backgroundColor =
      state.status === "error"
        ? new vscode.ThemeColor("statusBarItem.errorBackground")
        : alwaysApprove
          ? new vscode.ThemeColor("statusBarItem.warningBackground")
          : undefined;
  }

  dispose(): void {
    this.item.dispose();
  }
}
