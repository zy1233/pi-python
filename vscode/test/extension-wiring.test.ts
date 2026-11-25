import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

const registered = vi.hoisted(() => ({
  views: [] as { id: string; options: unknown }[],
  commands: new Map<string, (...args: unknown[]) => unknown>(),
  executedCommands: [] as unknown[][],
  openDialogResult: undefined as { fsPath: string }[] | undefined,
  configUpdates: [] as { key: string; value: unknown; target: unknown }[],
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
    ConfigurationTarget: { Global: 1 },
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
      showOpenDialog: async () => registered.openDialogResult,
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
      getConfiguration: () => ({
        get: (_key: string, fallback?: unknown) => fallback,
        update: async (key: string, value: unknown, target: unknown) => {
          registered.configUpdates.push({ key, value, target });
        },
      }),
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
    const tmp = mkdtempSync(path.join(os.tmpdir(), "zycode-ext-wiring-"));
    const context = {
      extension: { packageJSON: { version: "0.0.0", zycodeAgentVersion: "0.0.0" } },
      extensionPath: "/ext",
      extensionUri: { scheme: "file", fsPath: "/ext" },
      globalStorageUri: { scheme: "file", fsPath: path.join(tmp, "storage") },
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

      for (const cmd of [
        "zycode.chooseUserAvatar",
        "zycode.chooseAssistantAvatar",
        "zycode.chooseAccountAvatar",
        "zycode.resetUserAvatar",
        "zycode.resetAssistantAvatar",
        "zycode.chooseAssistantPreset",
      ]) {
        expect(registered.commands.has(cmd), cmd).toBe(true);
      }

      const sampleImg = path.join(tmp, "me.png");
      writeFileSync(sampleImg, Buffer.from([137, 80, 78, 71, 1, 2]));
      registered.configUpdates.length = 0;
      registered.openDialogResult = [{ fsPath: sampleImg }];
      await registered.commands.get("zycode.chooseUserAvatar")?.();
      expect(registered.configUpdates).toHaveLength(1);
      expect(registered.configUpdates[0]?.key).toBe("userAvatar");
      expect(String(registered.configUpdates[0]?.value)).toContain(path.join(tmp, "storage", "avatars", "user-"));
      expect(registered.configUpdates[0]?.target).toBe(1);

      await registered.commands.get("zycode.resetUserAvatar")?.();
      expect(registered.configUpdates.at(-1)).toEqual({ key: "userAvatar", value: "", target: 1 });
    } finally {
      await deactivate?.();
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});
