import path from "node:path";

import * as vscode from "vscode";

import type { DiffStore } from "../core/diff-store.js";
import type { DiffInfo } from "../core/diff-tracker.js";

export const DIFF_SCHEME = "zycode-diff";

function documentUri(key: string, side: "before" | "after", fileName: string): vscode.Uri {
  return vscode.Uri.from({
    scheme: DIFF_SCHEME,
    path: `/${encodeURIComponent(key)}/${side}/${fileName}`,
  });
}

/** Virtual documents for `vscode.diff`. The text is read from the controller's in-memory store; no file is written. */
export class DiffDocuments implements vscode.TextDocumentContentProvider {
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;

  constructor(private readonly store: () => DiffStore) {}

  provideTextDocumentContent(uri: vscode.Uri): string {
    const [, encodedKey, side] = uri.path.split("/");
    if (!encodedKey || (side !== "before" && side !== "after")) return "";
    return this.store().get(decodeURIComponent(encodedKey), side) ?? "";
  }

  async open(info: DiffInfo): Promise<void> {
    const fileName = path.posix.basename(info.title.replace(/ \(.*\)$/, "")) || "file";
    const before = documentUri(info.key, "before", fileName);
    const after = documentUri(info.key, "after", fileName);
    await vscode.commands.executeCommand("vscode.diff", before, after, `zycode: ${info.title}`, { preview: true });
  }

  dispose(): void {
    this.changed.dispose();
  }
}
