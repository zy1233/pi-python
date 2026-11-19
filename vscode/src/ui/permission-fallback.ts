import * as vscode from "vscode";

import {
  buildPermissionFallback,
  resolveFallbackChoice,
  type FallbackOptionItem,
} from "../core/permission-fallback.js";
import type { PermissionRequestView } from "../core/protocol.js";

export interface PermissionFallbackCallbacks {
  answer(requestId: string, optionId: string): boolean;
  reveal(): Promise<void>;
}

/**
 * Shows a modal warning dialog when a permission/trust request arrives while the chat view is not
 * visible, preventing the agent from silently hanging out of sight (design section 5.1).
 */
export class PermissionFallback {
  private readonly active = new Set<string>();

  show(request: PermissionRequestView, callbacks: PermissionFallbackCallbacks): void {
    if (this.active.has(request.requestId)) return;
    this.active.add(request.requestId);
    void this.present(request, callbacks);
  }

  forget(requestId: string): void {
    this.active.delete(requestId);
  }

  clear(): void {
    this.active.clear();
  }

  private async present(request: PermissionRequestView, callbacks: PermissionFallbackCallbacks): Promise<void> {
    const dialog = buildPermissionFallback(request);
    try {
      const chosen = await vscode.window.showWarningMessage<FallbackOptionItem>(
        dialog.message,
        { modal: true, detail: dialog.detail },
        ...dialog.items,
      );
      if (!this.active.has(request.requestId)) return;
      const action = resolveFallbackChoice(chosen);
      if (action.kind === "answer") {
        callbacks.answer(request.requestId, action.optionId);
      } else if (action.kind === "reveal") {
        await callbacks.reveal();
      }
    } finally {
      this.active.delete(request.requestId);
    }
  }
}
