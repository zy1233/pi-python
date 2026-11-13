import { chmodSync, existsSync, readFileSync, readdirSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import { parseWebviewMessage, type HostMessage } from "../src/core/protocol.js";
import { THINKING_LEVELS, noThinkingReason, readThinking, setThinkingLevelInText, writeThinkingLevel } from "../src/core/thinking.js";
import { TestHost, newController, ofType, readLog, tempDir, waitFor } from "./helpers.js";

const MODEL = '[model]\nprovider = "openai"\nid = "m"\n';

function edited(text: string, level: Parameters<typeof setThinkingLevelInText>[1]): string {
  const result = setThinkingLevelInText(text, level);
  if (!result.ok) throw new Error(result.reason);
  return result.text;
}

describe("reading thinking from agent.toml", () => {
  it("lists the levels of the agent, strongest first, as the pager does", () => {
    expect(THINKING_LEVELS.map((level) => level.id)).toEqual(["xhigh", "high", "medium", "low", "minimal", "off"]);
  });

  it("reads the level and says off when it is missing, as the agent does", () => {
    expect(readThinking(`thinking_level = "high"\n${MODEL}`)).toEqual({ ok: true, value: { level: "high", reasoning: undefined } });
    expect(readThinking(MODEL)).toEqual({ ok: true, value: { level: "off", reasoning: undefined } });
  });

  it("reports a level that the agent does not know, which the agent reads as off", () => {
    expect(readThinking('thinking_level = "ultra"\n')).toEqual({ ok: true, value: { level: "off", reasoning: undefined, invalidLevel: "ultra" } });
    expect(readThinking("thinking_level = 3\n")).toEqual({ ok: true, value: { level: "off", reasoning: undefined, invalidLevel: "number" } });
  });

  it("reads reasoning from [model] first, then from the top level, like CliConfig", () => {
    expect(readThinking('reasoning = true\n[model]\nreasoning = false\n')).toMatchObject({ ok: true, value: { reasoning: false } });
    expect(readThinking("reasoning = false\n")).toMatchObject({ ok: true, value: { reasoning: false } });
    expect(readThinking('[model]\nreasoning = "yes"\n')).toMatchObject({ ok: true, value: { reasoning: true } });
  });

  it("says why a model with reasoning = false has no picker", () => {
    const read = readThinking("[model]\nreasoning = false\n");
    if (!read.ok) throw new Error("must read");
    expect(noThinkingReason(read.value)).toMatch(/reasoning = false/);
    const open = readThinking("[model]\nreasoning = true\n");
    if (!open.ok) throw new Error("must read");
    expect(noThinkingReason(open.value)).toBeUndefined();
  });

  it("does not read a file that is not TOML", () => {
    const read = readThinking("this is = = not toml");
    expect(read.ok).toBe(false);
  });
});

describe("the edit of thinking_level", () => {
  it("replaces the value and keeps the comment, the spacing and the rest", () => {
    const text = `# my agent\nthinking_level   =   'low'   # keep me\n\n${MODEL}`;
    expect(edited(text, "high")).toBe(`# my agent\nthinking_level   =   "high"   # keep me\n\n${MODEL}`);
  });

  it("adds the key above the first table, above the comment that belongs to the table", () => {
    const text = `# top\nmax_turns = 5\n\n# the model\n${MODEL}`;
    expect(edited(text, "medium")).toBe(`# top\nmax_turns = 5\n\nthinking_level = "medium"\n\n# the model\n${MODEL}`);
  });

  it("adds the key to a file that has no table, and to an empty file", () => {
    expect(edited("max_turns = 5\n", "low")).toBe('max_turns = 5\nthinking_level = "low"\n');
    expect(edited("", "high")).toBe('thinking_level = "high"\n');
  });

  it("keeps Windows line ends", () => {
    const next = edited(`max_turns = 5\r\n${MODEL.replace(/\n/g, "\r\n")}`, "high");
    expect(next.replace(/\r\n/g, "")).not.toContain("\n");
    expect(next).toContain('thinking_level = "high"\r\n');
  });

  it("puts a key that is only in a table at the top level, not into the table", () => {
    const next = edited('[model]\nthinking_level = "low"\n', "high");
    expect(readThinking(next)).toMatchObject({ ok: true, value: { level: "high" } });
    expect(next.indexOf("thinking_level")).toBeLessThan(next.indexOf("[model]"));
  });

  it("is not fooled by a table header inside a multi-line string, or by a [ in an array", () => {
    const text = 'note = """\n[model]\n"""\nlist = [\n  [1, 2],\n]\n\n[model]\nid = "m"\n';
    const next = edited(text, "high");
    expect(readThinking(next)).toMatchObject({ ok: true, value: { level: "high" } });
  });

  it("refuses a form that it does not edit, and a file that is not TOML", () => {
    expect(setThinkingLevelInText('thinking_level = "high" # a\nthinking_level_x = 1\n', "low").ok).toBe(true);
    const inline = setThinkingLevelInText('thinking_level = ["high"]\n', "low");
    expect(inline.ok).toBe(false);
    const broken = setThinkingLevelInText("a = = 1\n", "low");
    expect(broken.ok).toBe(false);
  });

  it("changes nothing but the key (checked by parsing both texts)", () => {
    const text = `thinking_level = "off"\nmax_turns = 7\n${MODEL}[[models]]\nid = "x"\nprovider = "openai"\n[auto]\nclassifier = "rules"\n`;
    const next = edited(text, "xhigh");
    expect(next).toBe(text.replace('"off"', '"xhigh"'));
  });
});

const dirs: string[] = [];
afterEach(() => {
  dirs.length = 0;
});

describe("writing the file", () => {
  it("replaces the file, keeps the mode and leaves no temporary file", () => {
    const dir = tempDir(dirs, "toml-");
    const file = path.join(dir, "agent.toml");
    writeFileSync(file, `${MODEL}`);
    chmodSync(file, 0o640);
    expect(writeThinkingLevel(file, "high")).toEqual({ ok: true });
    expect(readFileSync(file, "utf8")).toBe(`thinking_level = "high"\n\n${MODEL}`);
    if (process.platform !== "win32") expect(statSync(file).mode & 0o777).toBe(0o640);
    expect(readdirSync(dir).sort()).toEqual(["agent.toml"]);
  });

  it.skipIf(process.platform === "win32")("follows a symbolic link and leaves the link", () => {
    const dir = tempDir(dirs, "toml-");
    const real = path.join(dir, "real.toml");
    const link = path.join(dir, "agent.toml");
    writeFileSync(real, MODEL);
    symlinkSync(real, link);
    expect(writeThinkingLevel(link, "low")).toEqual({ ok: true });
    expect(statSync(link).isFile()).toBe(true);
    expect(readFileSync(real, "utf8")).toContain('thinking_level = "low"');
  });

  it("does not create agent.toml, and does not write a file that it cannot edit", () => {
    const dir = tempDir(dirs, "toml-");
    const missing = path.join(dir, "agent.toml");
    expect(writeThinkingLevel(missing, "low")).toMatchObject({ ok: false });
    expect(existsSync(missing)).toBe(false);
    writeFileSync(missing, "a = = 1\n");
    expect(writeThinkingLevel(missing, "low")).toMatchObject({ ok: false });
    expect(readFileSync(missing, "utf8")).toBe("a = = 1\n");
  });
});

describe("the message of the page", () => {
  it("takes the six levels and nothing else", () => {
    for (const value of ["off", "minimal", "low", "medium", "high", "xhigh"]) {
      expect(parseWebviewMessage({ type: "setThinking", value })).toEqual({ type: "setThinking", value });
    }
    expect(parseWebviewMessage({ type: "setThinking", value: "max" })).toBeUndefined();
    expect(parseWebviewMessage({ type: "setThinking" })).toBeUndefined();
  });
});

// ----- the controller, with the fake agent

const hosts: TestHost[] = [];
const controllers: ChatController[] = [];
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

afterEach(async () => {
  restoreEnv?.();
  restoreEnv = undefined;
  await Promise.all(controllers.splice(0).map((c) => c.dispose()));
  for (const host of hosts.splice(0)) host.cleanup();
});

interface Rig {
  host: TestHost;
  controller: ChatController;
  messages: HostMessage[];
  toml: string;
  log: string;
}

function rig(options: { toml?: string | null; confirm?: boolean; env?: Record<string, string> } = {}): Rig {
  const dir = tempDir(dirs, "rig-");
  const toml = path.join(dir, "agent.toml");
  if (options.toml !== null) writeFileSync(toml, options.toml ?? `thinking_level = "low"\n${MODEL}`);
  const log = path.join(dir, "agent.log");
  withEnv({ FAKE_LOG: log, FAKE_TOML: toml, FAKE_STORE: path.join(dir, "sessions.json"), ...options.env });
  const host = new TestHost({ agentConfig: toml, confirm: options.confirm ?? true });
  hosts.push(host);
  const made = newController(host);
  controllers.push(made.controller);
  return { host, ...made, toml, log };
}

const thinkingOf = (controller: ChatController) => controller.stateMessage().thinking;
const settled = (controller: ChatController) => controller.currentStatus === "ready";
const notices = (messages: HostMessage[]) => ofType(messages, "notice").map((n) => n.text);
const starts = (log: string) => readLog(log).filter((line) => line.startsWith("start thinking_level="));

async function say(controller: ChatController, text: string): Promise<void> {
  await controller.send(text);
  await waitFor(() => settled(controller), "end of turn");
}

const agentText = (controller: ChatController) =>
  controller
    .transcript()
    .map((item) => (item.type === "agent" ? item.text : ""))
    .join("|");

describe("the thinking picker in the controller", () => {
  it("shows the level of the file, with the six levels, when the agent is ready", async () => {
    const { controller } = rig();
    expect(thinkingOf(controller)).toBeUndefined();
    await controller.start();
    expect(thinkingOf(controller)).toMatchObject({ current: "low" });
    expect(thinkingOf(controller)?.levels.map((l) => l.id)).toEqual(["xhigh", "high", "medium", "low", "minimal", "off"]);
    expect(thinkingOf(controller)?.disabled).toBeUndefined();
  });

  it("is disabled, with the reason, for a model that has reasoning = false", async () => {
    const { controller, host, toml } = rig({ toml: `thinking_level = "high"\n[model]\nreasoning = false\n` });
    await controller.start();
    expect(thinkingOf(controller)?.disabled).toMatch(/reasoning = false/);
    await controller.applyThinkingLevel("low");
    expect(host.confirmations).toHaveLength(0);
    expect(readFileSync(toml, "utf8")).toContain('thinking_level = "high"');
  });

  it("is disabled, with the reason, when agent.toml does not exist or is not TOML", async () => {
    const a = rig({ toml: null });
    await a.controller.start();
    expect(thinkingOf(a.controller)?.current).toBeUndefined();
    expect(thinkingOf(a.controller)?.disabled).toMatch(/does not exist/);
    const b = rig({ toml: "a = = 1\n" });
    await b.controller.start();
    expect(thinkingOf(b.controller)?.disabled).toMatch(/not valid TOML/);
  });

  it("names a level that the agent does not know, and shows off", async () => {
    const { controller } = rig({ toml: 'thinking_level = "ultra"\n' });
    await controller.start();
    expect(thinkingOf(controller)).toMatchObject({ current: "off" });
    expect(thinkingOf(controller)?.note).toMatch(/ultra/);
  });

  it("is disabled while an answer runs", async () => {
    const { controller } = rig();
    await controller.start();
    const turn = controller.send("hang");
    await waitFor(() => controller.transcript().some((item) => item.type === "permission"), "the permission card");
    expect(controller.currentStatus).toBe("busy");
    expect(thinkingOf(controller)?.disabled).toMatch(/Wait until the answer ends/);
    await controller.cancel();
    await turn;
  });

  it("asks first, edits the one line, restarts the agent and opens the same chat again", async () => {
    const { controller, host, toml, log, messages } = rig();
    await controller.start();
    await say(controller, "hello");
    await say(controller, "level");
    expect(agentText(controller)).toContain("level:low");
    expect(starts(log)).toEqual(["start thinking_level=low"]);

    await controller.applyThinkingLevel("xhigh");

    expect(host.confirmations).toHaveLength(1);
    expect(host.confirmations[0]?.detail).toContain(toml);
    expect(host.confirmations[0]?.detail).toContain('thinking_level = "xhigh"');
    expect(readFileSync(toml, "utf8")).toBe(`thinking_level = "xhigh"\n${MODEL}`);
    expect(starts(log)).toEqual(["start thinking_level=low", "start thinking_level=xhigh"]);
    expect(readLog(log).filter((line) => line.startsWith("session/load "))).toHaveLength(1);
    expect(controller.currentStatus).toBe("ready");
    expect(thinkingOf(controller)).toMatchObject({ current: "xhigh" });
    expect(notices(messages).at(-1)).toMatch(/Thinking level: xhigh\. The agent restarted and this chat was opened again\./);
    // The chat is the same one: its history is back, and the next turn runs at the new level.
    expect(controller.transcript().some((item) => item.type === "user" && item.text === "hello")).toBe(true);
    await say(controller, "level");
    expect(agentText(controller)).toContain("level:xhigh");
  });

  it("asks nothing more and changes nothing when the user says no", async () => {
    const { controller, host, toml, log } = rig({ confirm: false });
    await controller.start();
    await controller.applyThinkingLevel("high");
    expect(host.confirmations).toHaveLength(1);
    expect(readFileSync(toml, "utf8")).toContain('thinking_level = "low"');
    expect(starts(log)).toHaveLength(1);
    expect(thinkingOf(controller)).toMatchObject({ current: "low" });
  });

  it("does nothing for the level that is already set", async () => {
    const { controller, host } = rig();
    await controller.start();
    await controller.applyThinkingLevel("low");
    expect(host.confirmations).toHaveLength(0);
  });

  it("does not load a chat that has no message, and still restarts", async () => {
    const { controller, log } = rig();
    await controller.start();
    await controller.applyThinkingLevel("high");
    expect(starts(log)).toEqual(["start thinking_level=low", "start thinking_level=high"]);
    expect(readLog(log).filter((line) => line.startsWith("session/load "))).toHaveLength(0);
    expect(controller.currentStatus).toBe("ready");
  });

  it("leaves the file and the agent alone when the edit cannot be made safely", async () => {
    const text = 'thinking_level = ["low"]\n';
    const { controller, toml, log, messages } = rig({ toml: text });
    await controller.start();
    await say(controller, "hello");
    await controller.applyThinkingLevel("high");
    expect(readFileSync(toml, "utf8")).toBe(text);
    expect(starts(log)).toHaveLength(1);
    expect(notices(messages).at(-1)).toMatch(/was not changed/);
    expect(controller.currentStatus).toBe("ready");
  });

  it("keeps the level for a new chat, and a new controller (a restart of VS Code) starts with the file", async () => {
    const { controller, log, toml } = rig();
    await controller.start();
    await controller.applyThinkingLevel("medium");
    await controller.newChat();
    await say(controller, "level");
    expect(agentText(controller)).toContain("level:medium");
    expect(starts(log)).toHaveLength(2);
    await controller.dispose();

    const host = new TestHost({ agentConfig: toml, confirm: true });
    hosts.push(host);
    const made = newController(host);
    controllers.push(made.controller);
    await made.controller.start();
    expect(thinkingOf(made.controller)).toMatchObject({ current: "medium" });
    await say(made.controller, "level");
    expect(agentText(made.controller)).toContain("level:medium");
  });

  it("opens a past chat at the level of the running agent, not at the level it had", async () => {
    const { controller, log } = rig();
    await controller.start();
    await say(controller, "hello");
    await controller.newChat();
    await controller.applyThinkingLevel("high");
    await controller.refreshSessions();
    const rows = controller.sessions.kind === "ready" ? controller.sessions.rows : [];
    expect(rows.length).toBeGreaterThan(0);
    await controller.openPastSession(rows[rows.length - 1]!.id);
    await waitFor(() => settled(controller), "past chat");
    await say(controller, "level");
    expect(agentText(controller)).toContain("level:high");
    expect(starts(log)).toEqual(["start thinking_level=low", "start thinking_level=high"]);
  });

  it("says that Always approve is off again after the restart", async () => {
    const { controller, messages } = rig({ env: {} });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    await controller.applyThinkingLevel("high");
    expect(notices(messages).at(-1)).toMatch(/Always approve is off again/);
  });
});
