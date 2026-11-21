import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { HostMessage, PermissionRequestView, StateMessage } from "../src/core/protocol.js";

const mockState = vi.hoisted(() => {
  type StatusBarItemStub = {
    name?: string;
    command?: string;
    text: string;
    tooltip: string;
    backgroundColor?: { id: string } | undefined;
    shown: boolean;
    disposed: boolean;
    show(): void;
    dispose(): void;
  };

  type WatcherHandlers = {
    create: ((uri: { scheme: string; fsPath: string }) => void)[];
    delete: ((uri: { scheme: string; fsPath: string }) => void)[];
    change: ((uri: { scheme: string; fsPath: string }) => void)[];
  };

  return {
    statusBarItems: [] as StatusBarItemStub[],
    warningCalls: [] as {
      message: string;
      options: unknown;
      items: unknown[];
      resolve: (choice: unknown) => void;
    }[],
    infoMessages: [] as string[],
    executedCommands: [] as string[],
    isTrusted: true,
    gitignoreContent: "" as string | null,
    findFilesCalls: [] as { include: unknown; exclude: unknown; maxResults: unknown }[],
    findFilesResult: [] as { scheme: string; fsPath: string }[],
    activeTextEditor: undefined as
      | {
          document: { uri: { scheme: string; fsPath: string } };
          selection: {
            isEmpty: boolean;
            start: { line: number; character: number };
            end: { line: number; character: number };
          };
        }
      | undefined,
    tabGroups: [] as { tabs: { input: unknown }[] }[],
    configExcludes: {} as Record<string, Record<string, boolean>>,
    watcherHandlers: { create: [], delete: [], change: [] } as WatcherHandlers,
    configChangeHandlers: [] as ((ev: { affectsConfiguration(section: string): boolean }) => void)[],
    clipboardText: "",
    treeViewVisible: false,
  };
});

vi.mock("vscode", () => {
  class ThemeColor {
    constructor(public readonly id: string) {}
  }

  class ThemeIcon {
    constructor(public readonly id: string) {}
  }

  class TreeItem {
    id?: string;
    description?: string;
    iconPath?: unknown;
    tooltip?: string;
    contextValue?: string;
    command?: unknown;
    constructor(
      public readonly label: string,
      public readonly collapsibleState: number,
    ) {}
  }

  class EventEmitter<T> {
    private readonly listeners = new Set<(e: T) => void>();
    readonly event = (fn: (e: T) => void) => {
      this.listeners.add(fn);
      return { dispose: () => this.listeners.delete(fn) };
    };
    fire(e: T): void {
      for (const fn of this.listeners) fn(e);
    }
    dispose(): void {
      this.listeners.clear();
    }
  }

  class RelativePattern {
    constructor(
      public readonly base: unknown,
      public readonly pattern: string,
    ) {}
  }

  class TabInputText {
    constructor(public readonly uri: { scheme: string; fsPath: string }) {}
  }

  const Uri = {
    file: (fsPath: string) => ({ scheme: "file", fsPath, toString: () => `file://${fsPath}` }),
    from: (components: { scheme: string; path: string }) => ({
      scheme: components.scheme,
      path: components.path,
      fsPath: components.path,
      toString: () => `${components.scheme}:${components.path}`,
    }),
    joinPath: (base: { fsPath: string }, ...segments: string[]) => {
      const joined = path.join(base.fsPath, ...segments);
      return { scheme: "file", fsPath: joined, toString: () => `file://${joined}` };
    },
    parse: (url: string) => ({ scheme: "https", fsPath: url, toString: () => url }),
  };

  return {
    StatusBarAlignment: { Left: 1, Right: 2 },
    TreeItemCollapsibleState: { None: 0 },
    ThemeColor,
    ThemeIcon,
    TreeItem,
    EventEmitter,
    RelativePattern,
    TabInputText,
    Uri,
    commands: {
      executeCommand: vi.fn(async (cmd: string, ...args: unknown[]) => {
        mockState.executedCommands.push(
          args.length > 0 ? `${cmd}:${args.map((a) => String((a as { path?: string })?.path ?? a)).join(",")}` : cmd,
        );
      }),
    },
    env: {
      openExternal: vi.fn(async () => true),
      clipboard: {
        writeText: vi.fn(async (text: string) => {
          mockState.clipboardText = text;
        }),
      },
    },
    window: {
      get activeTextEditor() {
        return mockState.activeTextEditor;
      },
      get tabGroups() {
        return { all: mockState.tabGroups };
      },
      createTreeView: vi.fn(() => ({
        message: undefined as string | undefined,
        get visible() {
          return mockState.treeViewVisible;
        },
        dispose() {},
      })),
      createStatusBarItem: vi.fn(() => {
        const item = {
          text: "",
          tooltip: "",
          backgroundColor: undefined,
          shown: false,
          disposed: false,
          show() {
            this.shown = true;
          },
          dispose() {
            this.disposed = true;
          },
        };
        mockState.statusBarItems.push(item);
        return item;
      }),
      showWarningMessage: vi.fn((message: string, options: unknown, ...items: unknown[]) => {
        return new Promise((resolve) => {
          mockState.warningCalls.push({ message, options, items, resolve });
        });
      }),
      showInformationMessage: vi.fn(async (message: string) => {
        mockState.infoMessages.push(message);
        return undefined;
      }),
    },
    workspace: {
      get isTrusted() {
        return mockState.isTrusted;
      },
      getConfiguration: vi.fn((section: string) => ({
        get: (key: string) => (key === "exclude" ? mockState.configExcludes[section] : undefined),
      })),
      fs: {
        readFile: vi.fn(async () => {
          if (mockState.gitignoreContent === null) {
            throw new Error("ENOENT");
          }
          return new TextEncoder().encode(mockState.gitignoreContent);
        }),
      },
      findFiles: vi.fn(async (include: unknown, exclude: unknown, maxResults: unknown) => {
        mockState.findFilesCalls.push({ include, exclude, maxResults });
        return mockState.findFilesResult;
      }),
      createFileSystemWatcher: vi.fn(() => ({
        onDidCreate: (fn: (uri: { scheme: string; fsPath: string }) => void) => {
          mockState.watcherHandlers.create.push(fn);
          return { dispose() {} };
        },
        onDidDelete: (fn: (uri: { scheme: string; fsPath: string }) => void) => {
          mockState.watcherHandlers.delete.push(fn);
          return { dispose() {} };
        },
        onDidChange: (fn: (uri: { scheme: string; fsPath: string }) => void) => {
          mockState.watcherHandlers.change.push(fn);
          return { dispose() {} };
        },
        dispose() {},
      })),
      onDidChangeConfiguration: vi.fn((fn: (ev: { affectsConfiguration(section: string): boolean }) => void) => {
        mockState.configChangeHandlers.push(fn);
        return { dispose() {} };
      }),
    },
  };
});

import { DiffStore } from "../src/core/diff-store.js";
import { ChatViewProvider } from "../src/ui/chat-view.js";
import { DiffDocuments } from "../src/ui/diff-documents.js";
import { SessionsView } from "../src/ui/sessions-view.js";
import { StatusBar } from "../src/ui/status-bar.js";
import { WorkspaceMentions } from "../src/ui/workspace-mentions.js";

function makeUri(fsPath: string) {
  return { scheme: "file", fsPath, toString: () => `file://${fsPath}` };
}

beforeEach(() => {
  mockState.statusBarItems.length = 0;
  mockState.warningCalls.length = 0;
  mockState.infoMessages.length = 0;
  mockState.executedCommands.length = 0;
  mockState.isTrusted = true;
  mockState.gitignoreContent = "";
  mockState.findFilesCalls.length = 0;
  mockState.findFilesResult = [];
  mockState.activeTextEditor = undefined;
  mockState.tabGroups = [];
  mockState.configExcludes = {};
  mockState.watcherHandlers = { create: [], delete: [], change: [] };
  mockState.configChangeHandlers.length = 0;
  mockState.clipboardText = "";
  mockState.treeViewVisible = false;
});

describe("StatusBar UI wiring", () => {
  it("switches to warning badge and background when pendingPermissions > 0 and restores normal status when cleared", () => {
    const bar = new StatusBar("zycode.focus");
    const item = mockState.statusBarItems[0]!;
    expect(item.shown).toBe(true);
    expect(item.text).toBe("$(sparkle) zycode: stopped");
    expect(item.backgroundColor).toBeUndefined();

    const waitingState: StateMessage = {
      type: "state",
      status: "busy",
      canRestart: true,
      pendingPermissions: 2,
    };
    bar.update(waitingState);
    expect(item.text).toBe("$(warning) zycode: approval needed (2)");
    expect(item.tooltip).toContain("Waiting for your approval on 2 requests in the zycode chat.");
    expect(item.backgroundColor).toEqual({ id: "statusBarItem.warningBackground" });

    bar.update({ type: "state", status: "busy", canRestart: true });
    expect(item.text).toBe("$(sync~spin) zycode: busy");
    expect(item.backgroundColor).toBeUndefined();

    bar.dispose();
    expect(item.disposed).toBe(true);
  });
});

describe("ChatViewProvider permission fallback UI wiring", () => {
  function makeProvider() {
    let listener: ((msg: HostMessage) => void) | undefined;
    const answers: { requestId: string; optionId: string }[] = [];
    const controller = {
      onMessage(fn: (msg: HostMessage) => void) {
        listener = fn;
        return { dispose() {} };
      },
      answerPermission(requestId: string, optionId: string) {
        answers.push({ requestId, optionId });
        return true;
      },
    } as unknown as ChatController;

    const provider = new ChatViewProvider(makeUri("/ext") as never, controller, {
      showLog() {},
      async showDiff() {},
      notify() {},
      openGuide() {},
      openAgentConfig() {},
    });

    return {
      provider,
      answers,
      emit(msg: HostMessage) {
        listener?.(msg);
      },
    };
  }

  const samplePerm: PermissionRequestView = {
    requestId: "req-1",
    kind: "tool",
    toolCallId: "tc-1",
    title: "bash",
    summary: "pytest",
    canDiff: false,
    options: [
      { id: "allow-once", name: "Allow once", kind: "allow_once" },
      { id: "reject-once", name: "Reject", kind: "reject_once" },
    ],
  };

  it("opens a modal warning only when the webview is not visible and notifies if answered after permissionDone", async () => {
    const { provider, answers, emit } = makeProvider();
    let visible = true;
    const fakeView = {
      get visible() {
        return visible;
      },
      show: vi.fn(),
      webview: {
        options: {},
        html: "",
        cspSource: "vscode-webview:",
        asWebviewUri: (u: unknown) => u,
        postMessage: vi.fn(async () => true),
        onDidReceiveMessage: () => ({ dispose() {} }),
      },
      onDidDispose: () => ({ dispose() {} }),
    };

    provider.resolveWebviewView(fakeView as never);

    // Visible view: no fallback modal.
    emit({ type: "permission", ...samplePerm });
    expect(mockState.warningCalls).toHaveLength(0);

    // Hidden view: opens fallback modal and forwards selected answer.
    visible = false;
    emit({ type: "permission", ...samplePerm, requestId: "req-2" });
    expect(mockState.warningCalls).toHaveLength(1);
    const modal2 = mockState.warningCalls[0]!;
    modal2.resolve(modal2.items[0]);
    await Promise.resolve();
    expect(answers).toEqual([{ requestId: "req-2", optionId: "allow-once" }]);

    // Hidden view where permissionDone arrives before the modal resolves: shows stale info message.
    emit({ type: "permission", ...samplePerm, requestId: "req-3" });
    expect(mockState.warningCalls).toHaveLength(2);
    const modal3 = mockState.warningCalls[1]!;
    emit({ type: "permissionDone", requestId: "req-3", outcome: "selected", optionId: "allow-once" });
    modal3.resolve(modal3.items[0]);
    await Promise.resolve();
    expect(answers).toHaveLength(1);
    expect(mockState.infoMessages).toEqual(["This question was already answered or cancelled."]);

    provider.dispose();
  });
});

describe("WorkspaceMentions UI wiring", () => {
  const folder = path.resolve("/workspace/proj");
  const fileUri = (rel: string) => makeUri(path.join(folder, rel));

  it("reads .gitignore, filters ignored files, badges secret files, and ignores watcher events on ignored paths", async () => {
    mockState.gitignoreContent = "dist/\n*.log\n!important.log\n";
    mockState.findFilesResult = [
      fileUri("src/app.ts"),
      fileUri("dist/bundle.js"),
      fileUri("debug.log"),
      fileUri("important.log"),
      fileUri(".env"),
      fileUri(".env.example"),
    ];

    const mentions = new WorkspaceMentions();
    const results = await mentions.search(folder, "", 20);
    const byPath = new Map(results.map((r) => [r.path, r]));

    expect(byPath.has("src/app.ts")).toBe(true);
    expect(byPath.has("important.log")).toBe(true);
    expect(byPath.has("dist/bundle.js")).toBe(false);
    expect(byPath.has("debug.log")).toBe(false);
    expect(byPath.get(".env")?.badge).toBe("secret");
    expect(byPath.get(".env.example")?.badge).toBeUndefined();
    expect(mockState.findFilesCalls).toHaveLength(1);

    // Cached search does not call findFiles again.
    await mentions.search(folder, "app", 10);
    expect(mockState.findFilesCalls).toHaveLength(1);

    // Watcher event on a gitignored path must NOT invalidate the cache.
    mockState.watcherHandlers.create[0]?.(fileUri("dist/new.js"));
    await mentions.search(folder, "app", 10);
    expect(mockState.findFilesCalls).toHaveLength(1);

    // Watcher event on an unignored path invalidates the cache.
    mockState.watcherHandlers.create[0]?.(fileUri("src/new.ts"));
    await mentions.search(folder, "app", 10);
    expect(mockState.findFilesCalls).toHaveLength(2);

    // .gitignore change event invalidates the cache.
    mockState.watcherHandlers.change[0]?.(fileUri(".gitignore"));
    await mentions.search(folder, "app", 10);
    expect(mockState.findFilesCalls).toHaveLength(3);

    mentions.dispose();
  });
});

describe("ChatViewProvider host clipboard & SessionsView timer polish", () => {
  it("writes copy messages to vscode.env.clipboard and skips SessionsView timer refresh when hidden or empty", async () => {
    let receiveMessage: ((raw: unknown) => void) | undefined;
    const controller = {
      onMessage: () => ({ dispose() {} }),
      onSessionsChanged: () => ({ dispose() {} }),
      sessions: { kind: "ready", rows: [{ id: "s1", cwd: "/p", title: "Chat 1", updatedAt: 1000 }], truncated: false },
      currentSessionId: "s1",
      canDeleteSessions: true,
    } as unknown as ChatController;

    const provider = new ChatViewProvider(makeUri("/ext") as never, controller, {
      showLog() {},
      async showDiff() {},
      notify() {},
      openGuide() {},
      openAgentConfig() {},
    });
    provider.resolveWebviewView({
      visible: true,
      show: vi.fn(),
      webview: {
        options: {},
        html: "",
        cspSource: "vscode-webview:",
        asWebviewUri: (u: unknown) => u,
        postMessage: vi.fn(async () => true),
        onDidReceiveMessage: (fn: (raw: unknown) => void) => {
          receiveMessage = fn;
          return { dispose() {} };
        },
      },
      onDidDispose: () => ({ dispose() {} }),
    } as never);

    receiveMessage?.({ type: "copy", text: "copied from webview" });
    await Promise.resolve();
    expect(mockState.clipboardText).toBe("copied from webview");
    provider.dispose();

    vi.useFakeTimers();
    try {
      const sessions = new SessionsView(controller);
      let changes = 0;
      sessions.onDidChangeTreeData(() => {
        changes++;
      });

      // Hidden view: timer does not fire tree data refresh.
      mockState.treeViewVisible = false;
      vi.advanceTimersByTime(60_000);
      expect(changes).toBe(0);

      // Visible view with rows: timer fires refresh.
      mockState.treeViewVisible = true;
      vi.advanceTimersByTime(60_000);
      expect(changes).toBe(1);

      sessions.dispose();
    } finally {
      vi.useRealTimers();
    }

    const docs = new DiffDocuments(() => new DiffStore());
    await docs.open({ key: "call-1:result", title: "src\\core\\app.ts (edited)" });
    expect(mockState.executedCommands.at(-1)).toContain("/call-1%3Aresult/before/app.ts");
    expect(mockState.executedCommands.at(-1)).toContain("/call-1%3Aresult/after/app.ts");
    docs.dispose();
  });
});
