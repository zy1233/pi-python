import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { HostMessage } from "../src/core/protocol.js";
import type { SessionRow } from "../src/core/session-list.js";
import { TestHost, newController, ofType, readLog, tempDir, waitFor } from "./helpers.js";

const hosts: TestHost[] = [];
const controllers: ChatController[] = [];
const dirs: string[] = [];
let restoreEnv: (() => void) | undefined;

function withEnv(values: Record<string, string>): void {
  const saved: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(values)) {
    saved[key] = process.env[key];
    process.env[key] = value;
  }
  restoreEnv = () => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

function setup(options: ConstructorParameters<typeof TestHost>[0] = {}) {
  const host = new TestHost(options);
  hosts.push(host);
  const made = newController(host);
  controllers.push(made.controller);
  return { host, ...made };
}

afterEach(async () => {
  restoreEnv?.();
  restoreEnv = undefined;
  await Promise.all(controllers.splice(0).map((c) => c.dispose()));
  for (const host of hosts.splice(0)) host.cleanup();
  dirs.length = 0;
});

const rowsOf = (controller: ChatController): SessionRow[] => {
  const state = controller.sessions;
  return state.kind === "ready" || state.kind === "loading" ? state.rows : [];
};
const settled = (controller: ChatController) => controller.currentStatus === "ready" && controller.sessions.kind !== "loading";
const notices = (messages: HostMessage[]) => ofType(messages, "notice").map((n) => n.text);

/** Sends a prompt and waits until the turn has ended and the list has been read again. */
async function say(controller: ChatController, text: string): Promise<void> {
  await controller.send(text);
  await waitFor(() => settled(controller), "end of turn");
  await controller.refreshSessions();
}

function store(): string {
  return path.join(tempDir(dirs, "store-"), "sessions.json");
}

describe("the list of past sessions", () => {
  it("is empty for a folder with no session, and the new chat is not in it yet", async () => {
    withEnv({ FAKE_STORE: store() });
    const { controller } = setup();
    await controller.start();
    await controller.refreshSessions();
    expect(controller.sessions.kind).toBe("empty");
  });

  it("shows a chat after its first message, with its first prompt as the title, newest first", async () => {
    withEnv({ FAKE_STORE: store() });
    const { controller } = setup();
    await controller.start();
    await say(controller, "first topic");
    await controller.newChat();
    await say(controller, "second topic");
    const rows = rowsOf(controller);
    expect(rows.map((r) => r.title)).toEqual(["second topic", "first topic"]);
    expect(rows[0]?.updatedAt).toBeDefined();
    expect(controller.currentSessionId).toBe(rows[0]?.id);
  });

  it("reads every page", async () => {
    const file = store();
    withEnv({ FAKE_STORE: file, FAKE_PAGE_SIZE: "2" });
    const { controller } = setup();
    await controller.start();
    for (const name of ["a", "b", "c", "d", "e"]) {
      await say(controller, name);
      await controller.newChat();
    }
    await controller.refreshSessions();
    expect(rowsOf(controller).map((r) => r.title)).toEqual(["e", "d", "c", "b", "a"]);
  });

  it("shows the sessions of this folder only", async () => {
    const file = store();
    withEnv({ FAKE_STORE: file });
    const one = setup();
    await one.controller.start();
    await say(one.controller, "in folder one");
    await one.controller.dispose();
    const two = setup();
    await two.controller.start();
    await two.controller.refreshSessions();
    expect(two.controller.sessions.kind).toBe("empty");
    await say(two.controller, "in folder two");
    expect(rowsOf(two.controller).map((r) => r.title)).toEqual(["in folder two"]);
  });

  it("drops a row that the agent gives for another folder, even when the agent ignores the filter", async () => {
    const file = store();
    withEnv({ FAKE_STORE: file });
    const one = setup();
    await one.controller.start();
    await say(one.controller, "secret of folder one");
    const foreignId = one.controller.currentSessionId!;
    await one.controller.dispose();
    restoreEnv?.();
    withEnv({ FAKE_STORE: file, FAKE_IGNORE_CWD: "1" });
    const two = setup();
    await two.controller.start();
    await two.controller.refreshSessions();
    expect(two.controller.sessions.kind).toBe("empty");
    await two.controller.openPastSession(foreignId);
    expect(notices(two.messages).some((t) => t.includes("not in the list"))).toBe(true);
    expect(two.controller.transcript().some((i) => i.type === "user" && i.text.includes("secret"))).toBe(false);
  });

  it("shows the error state when the agent cannot list", async () => {
    withEnv({ FAKE_STORE: store(), FAKE_LIST_ERROR: "1" });
    const { controller } = setup();
    await controller.start();
    await controller.refreshSessions();
    const state = controller.sessions;
    expect(state.kind).toBe("error");
    expect(state.kind === "error" && state.message).toContain("cannot read");
    expect(controller.currentStatus).toBe("ready");
  });

  it("says so when the agent has no session/list", async () => {
    withEnv({ FAKE_STORE: store(), FAKE_NO_LIST: "1" });
    const { controller } = setup();
    await controller.start();
    await controller.refreshSessions();
    expect(controller.sessions.kind).toBe("unavailable");
  });

  it("says so in an untrusted workspace and with no folder", async () => {
    const untrusted = setup({ trusted: false });
    await untrusted.controller.start();
    await untrusted.controller.refreshSessions();
    expect(untrusted.controller.sessions).toEqual({ kind: "unavailable", reason: "Past chats are not available in Restricted Mode." });
    const none = setup({ folder: null });
    await none.controller.start();
    await none.controller.refreshSessions();
    expect(none.controller.sessions.kind).toBe("unavailable");
  });

  it("tells its listeners when it changes", async () => {
    withEnv({ FAKE_STORE: store() });
    const { controller } = setup();
    let calls = 0;
    controller.onSessionsChanged(() => calls++);
    await controller.start();
    await say(controller, "x");
    expect(calls).toBeGreaterThan(1);
  });
});

describe("open a past session", () => {
  it("shows the history in the chat, and the chat goes on in that session", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_STORE: store(), FAKE_LOG: log });
    const { controller } = setup();
    await controller.start();
    await say(controller, "hello");
    const old = controller.currentSessionId!;
    await controller.newChat();
    await say(controller, "other");
    await controller.openPastSession(old);
    expect(controller.currentSessionId).toBe(old);
    const items = controller.transcript();
    expect(items.find((i) => i.type === "user")).toMatchObject({ text: "hello" });
    expect(items.find((i) => i.type === "agent")).toMatchObject({ text: "Hello" });
    expect(readLog(log).some((l) => l.startsWith(`session/load ${old} `))).toBe(true);
    await say(controller, "again");
    expect(readLog(log)).toContain("session/prompt again");
    expect(controller.transcript().filter((i) => i.type === "user")).toHaveLength(2);
  });

  it("does not show the replayed messages twice and does not add diff buttons to replayed tool calls", async () => {
    withEnv({ FAKE_STORE: store() });
    const { controller } = setup();
    await controller.start();
    await say(controller, "hello");
    const id = controller.currentSessionId!;
    await controller.newChat();
    await controller.openPastSession(id);
    await controller.openPastSession(id);
    expect(controller.transcript().filter((i) => i.type === "user")).toHaveLength(1);
  });

  it("refuses a session that the agent says belongs to another folder, and shows none of it", async () => {
    const file = store();
    withEnv({ FAKE_STORE: file });
    const first = setup();
    await first.controller.start();
    await say(first.controller, "mine");
    const id = first.controller.currentSessionId!;
    const folder = first.host.sessionFolder()!;
    await first.controller.dispose();
    restoreEnv?.();
    withEnv({ FAKE_STORE: file, FAKE_LOAD_CWD: "/somewhere/else" });
    const second = setup({ folder });
    await second.controller.start();
    await second.controller.refreshSessions();
    expect(rowsOf(second.controller)).toHaveLength(1);
    await second.controller.openPastSession(id);
    expect(notices(second.messages).some((t) => t.includes("another folder"))).toBe(true);
    expect(second.controller.transcript().some((i) => i.type === "user" && i.text === "mine")).toBe(false);
    expect(second.controller.currentStatus).toBe("ready");
    expect(second.controller.currentSessionId).not.toBe(id);
  });

  it("survives a restart of the extension: a new controller lists and loads the old chat", async () => {
    const file = store();
    withEnv({ FAKE_STORE: file });
    const first = setup();
    await first.controller.start();
    await say(first.controller, "before restart");
    const id = first.controller.currentSessionId!;
    const folder = first.host.sessionFolder()!;
    await first.controller.dispose();
    const second = setup({ folder });
    await second.controller.start();
    await second.controller.refreshSessions();
    expect(rowsOf(second.controller).map((r) => r.id)).toEqual([id]);
    await second.controller.openPastSession(id);
    expect(second.controller.transcript().find((i) => i.type === "user")).toMatchObject({ text: "before restart" });
    expect(second.controller.currentStatus).toBe("ready");
  });

  it("waits while an answer runs", async () => {
    withEnv({ FAKE_STORE: store() });
    const { controller, messages } = setup();
    await controller.start();
    await say(controller, "one");
    const id = controller.currentSessionId!;
    await controller.newChat();
    const turn = controller.send("hang");
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    await controller.openPastSession(id);
    expect(controller.currentSessionId).not.toBe(id);
    expect(notices(messages).some((t) => t.includes("Wait until"))).toBe(true);
    await controller.cancel();
    await turn;
    expect(controller.currentStatus).toBe("ready");
  });
});

describe("delete a session", () => {
  it("asks first, and does nothing on No", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_STORE: store(), FAKE_LOG: log });
    const { controller, host } = setup({ confirm: false });
    await controller.start();
    await say(controller, "keep me");
    const id = controller.currentSessionId!;
    await controller.newChat();
    await controller.refreshSessions();
    await controller.deleteSession(id);
    expect(host.confirmations).toHaveLength(1);
    expect(host.confirmations[0]?.detail).toContain("keep me");
    expect(host.confirmations[0]?.detail).toContain("cannot be undone");
    expect(rowsOf(controller).map((r) => r.id)).toEqual([id]);
    expect(readLog(log).some((l) => l.startsWith("pi/session/delete"))).toBe(false);
  });

  it("deletes after Yes, and the list updates", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_STORE: store(), FAKE_LOG: log });
    const { controller } = setup({ confirm: true });
    await controller.start();
    await say(controller, "old one");
    const id = controller.currentSessionId!;
    await controller.newChat();
    await say(controller, "new one");
    expect(rowsOf(controller)).toHaveLength(2);
    await controller.deleteSession(id);
    await controller.refreshSessions();
    expect(rowsOf(controller).map((r) => r.title)).toEqual(["new one"]);
    expect(readLog(log)).toContain(`pi/session/delete ${id}`);
  });

  it("deletes the open chat, and a new empty chat starts", async () => {
    withEnv({ FAKE_STORE: store() });
    const { controller } = setup({ confirm: true });
    await controller.start();
    await say(controller, "open and doomed");
    const id = controller.currentSessionId!;
    await controller.deleteSession(id);
    expect(controller.currentStatus).toBe("ready");
    expect(controller.currentSessionId).not.toBe(id);
    expect(controller.transcript().filter((i) => i.type === "user")).toHaveLength(0);
    await controller.refreshSessions();
    expect(controller.sessions.kind).toBe("empty");
  });

  it("says so when the agent has no pi/session/delete, and then offers no delete", async () => {
    withEnv({ FAKE_STORE: store(), FAKE_NO_DELETE: "1" });
    const { controller, messages } = setup({ confirm: true });
    await controller.start();
    await say(controller, "stays");
    const id = controller.currentSessionId!;
    await controller.newChat();
    await controller.refreshSessions();
    expect(controller.canDeleteSessions).toBe(true);
    await controller.deleteSession(id);
    expect(controller.canDeleteSessions).toBe(false);
    expect(notices(messages).some((t) => t.includes("cannot delete"))).toBe(true);
    expect(rowsOf(controller).map((r) => r.id)).toEqual([id]);
  });
});
