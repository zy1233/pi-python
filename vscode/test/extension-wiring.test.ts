import { describe, expect, it, vi } from "vitest";

const registered = vi.hoisted(() => ({
  views: [] as { id: string; options: unknown }[],
  commands: new Map<string, (...args: unknown[]) => unknown>(),
  executedCommands: [] as unknown[][],
}));

vi.mock("vscode", () => {
  // Every API that `activate` touches returns a no-op value; the one that the test reads is recorded.
  const noop: unknown = new Proxy(function () {}, {
    get: (_target, key) => (key === "dispose" ? () => {} : key === "then" ? undefined : noop),
    set: () => true,
    apply: () => noop,
    construct: () => ({}) as object,
  });
  const event = () => ({ dispose() {} });
  class EventEmitter {
    readonly event = event;
    fire(): void {}
    dispose(): void {}
  }
  const section = (target: object): unknown =>
    new Proxy(target, { get: (t, key) => (key in t ? (t as Record<string | symbol, unknown>)[key] : noop) });
  return {
    EventEmitter,
    StatusBarAlignment: { Left: 1, Right: 2 },
    TreeItemCollapsibleState: { None: 0 },
    ThemeColor: class {},
    ThemeIcon: class {},
    TreeItem: class {},
    Uri: { file: (fsPath: string) => ({ scheme: "file", fsPath }), joinPath: () => ({ scheme: "file", fsPath: "/x" }) },
    window: section({
      createOutputChannel: () => section({ appendLine() {}, dispose() {} }),
      registerWebviewViewProvider: (id: string, _provider: unknown, options: unknown) => {
        registered.views.push({ id, options });
        return { dispose() {} };
      },
      onDidChangeActiveTextEditor: event,
      onDidChangeTextEditorSelection: event,
      onDidChangeWindowState: event,
    }),
    workspace: section({
      workspaceFolders: undefined,
      isTrusted: true,
      onDidChangeWorkspaceFolders: event,
      onDidChangeConfiguration: event,
      onDidGrantWorkspaceTrust: event,
      getConfiguration: () => ({ get: (_key: string, fallback?: unknown) => fallback }),
    }),
    commands: section({
      registerCommand: (id: string, cb: (...args: unknown[]) => unknown) => {
        registered.commands.set(id, cb);
        return { dispose() {} };
      },
      executeCommand: async (...args: unknown[]) => {
        registered.executedCommands.push(args);
        return undefined;
      },
    }),
    authentication: section({ onDidChangeSessions: event, getSession: async () => undefined }),
    env: section({}),
  };
});

import { CHAT_VIEW_ID } from "../src/ui/chat-view.js";
import { activate, deactivate } from "../src/extension.js";

describe("extension wiring", () => {
  it("registers the chat view with retainContextWhenHidden: true, so a hidden view keeps its draft and chat", async () => {
    const context = {
      extension: { packageJSON: { version: "0.0.0", zycodeAgentVersion: "0.0.0" } },
      extensionPath: "/ext",
      extensionUri: { scheme: "file", fsPath: "/ext" },
      subscriptions: [] as { dispose(): void }[],
      globalState: { get: () => undefined, update: async () => undefined },
    };
    activate(context as never);
    try {
      expect(registered.views).toHaveLength(1);
      expect(registered.views[0]?.id).toBe(CHAT_VIEW_ID);
      expect(registered.views[0]?.options).toEqual({ webviewOptions: { retainContextWhenHidden: true } });

      registered.executedCommands.length = 0;
      await registered.commands.get("zycode.openSettings")?.();
      expect(registered.executedCommands).toEqual([["workbench.action.openSettings", "@ext:zycode.zycode"]]);
    } finally {
      await deactivate?.();
    }
  });
});
