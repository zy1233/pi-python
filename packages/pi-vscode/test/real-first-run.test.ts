// The real Python agent, a fake model server on 127.0.0.1 (packaging/agent/smoke.py) and the real
// `--setup` dialog, typed in a pseudo-terminal. Off by default: ZYCODE_REAL_AGENT=1 (`npm run test:agent`).
// Linux and macOS only (the pseudo-terminal helper uses `pty`).
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { SessionRow } from "../src/core/session-list.js";
import { TestHost, newController, ofType, tempDir, waitFor, type TestHostOptions } from "./helpers.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const python = process.env.ZYCODE_REAL_AGENT_PYTHON ?? path.join(repo, ".venv", "bin", "python");
const enabled = process.env.ZYCODE_REAL_AGENT === "1" && existsSync(python) && process.platform !== "win32";

const KEY = "sk-first-run-GOOD-0123456789";
const WRONG_KEY = "sk-first-run-WRONG-0123456789";
const dirs: string[] = [];
const cleanups: (() => Promise<void> | void)[] = [];
const saved: Record<string, string | undefined> = {};

function setEnv(values: Record<string, string | undefined>): void {
  for (const [key, value] of Object.entries(values)) {
    if (!(key in saved)) saved[key] = process.env[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup();
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
    delete saved[key];
  }
});

async function fakeModel(expectKey: string): Promise<number> {
  const child: ChildProcess = spawn(python, [path.join(here, "support", "fake_model.py"), "--expect-key", expectKey], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  cleanups.push(() => {
    child.kill("SIGKILL");
  });
  return new Promise((resolve, reject) => {
    let buffer = "";
    child.stdout!.on("data", (chunk: Buffer) => {
      buffer += chunk.toString();
      const match = /PORT (\d+)/.exec(buffer);
      if (match) resolve(Number(match[1]));
    });
    child.on("exit", (code) => reject(new Error(`fake model exited with ${code}`)));
  });
}

interface Typed {
  prompt: string;
  answer: string;
}

/** The setup of the agent, in a terminal, with typed answers. Returns what the terminal showed. */
function setupTerminal(typed: Typed[], log: string[]) {
  return async (request: { command: string; args: string[]; env: Record<string, string> }) => {
    const answers = typed.map((t) => `${t.prompt}=${t.answer}`);
    const run = spawnSync(python, [path.join(here, "support", "pty_answers.py"), ...answers, "--", request.command, ...request.args], {
      encoding: "utf8",
      env: { ...process.env, ...request.env },
      timeout: 90_000,
    });
    log.push(run.stdout, run.stderr);
    return { exitCode: run.status ?? undefined };
  };
}

const SETUP_FIRST: (port: number, key: string) => Typed[] = (port, key) => [
  { prompt: "Number: ", answer: "1" },
  { prompt: "Model id, as the provider names it: ", answer: "first-run-model" },
  { prompt: "Base URL (Enter: the provider's own): ", answer: `http://127.0.0.1:${port}/v1` },
  { prompt: "Variable that holds the key (Enter: OPENAI_API_KEY): ", answer: "ZYCODE_TEST_KEY" },
  { prompt: "API key for ZYCODE_TEST_KEY (what you type is hidden): ", answer: key },
];

function start(extra: { home?: string; folder?: string; setup?: TestHostOptions["setup"] } = {}) {
  const home = extra.home ?? tempDir(dirs, "pihome-");
  setEnv({
    PI_HOME: home,
    PI_USE_MOCK: undefined,
    PI_TEST_PERMISSION: undefined,
    PI_TEST_TOOL_CALL: undefined,
    ZYCODE_TEST_KEY: undefined,
    OPENAI_API_KEY: undefined,
    DEEPSEEK_API_KEY: undefined,
    ANTHROPIC_API_KEY: undefined,
  });
  const host = new TestHost({
    folder: extra.folder,
    settings: { agentPath: python, agentArgs: ["-m", "pi_agent_cli"] },
    setup: extra.setup,
    confirm: true,
  });
  const made = newController(host);
  cleanups.push(() => made.controller.dispose(), () => host.cleanup());
  return { host, home, ...made };
}

const replyText = (controller: ChatController) =>
  controller
    .transcript()
    .filter((i) => i.type === "agent")
    .map((i) => (i.type === "agent" ? i.text : ""))
    .join("");

const rowsOf = (controller: ChatController): SessionRow[] => {
  const state = controller.sessions;
  return state.kind === "ready" ? state.rows : [];
};

function allFilesText(dir: string): string {
  let text = "";
  for (const name of readdirSync(dir, { recursive: true, encoding: "utf8" })) {
    const file = path.join(dir, name);
    if (statSync(file).isFile() && name !== "local.env") text += readFileSync(file, "utf8");
  }
  return text;
}

describe.skipIf(!enabled)("first run with the real agent and a clean home", () => {
  it("goes from an empty home to a first answer: needs-setup, the real --setup, restart, reply", async () => {
    const port = await fakeModel(KEY);
    const terminal: string[] = [];
    const { controller, host, home, messages } = start({ setup: setupTerminal(SETUP_FIRST(port, KEY), terminal) });
    await controller.start();

    const first = controller.stateMessage();
    expect(first.status).toBe("needs-setup");
    expect(first.setup).toEqual({ reason: "no-model" });
    expect(existsSync(path.join(home, "agent.toml"))).toBe(false);

    await controller.runSetup();
    expect(host.setupRequests).toHaveLength(1);
    expect(host.setupRequests[0]?.args).toContain("--setup");
    expect(controller.currentStatus).toBe("ready");

    const toml = readFileSync(path.join(home, "agent.toml"), "utf8");
    expect(toml).toContain('api_key_env = "ZYCODE_TEST_KEY"');
    expect(toml).not.toContain(KEY);
    expect(readFileSync(path.join(home, "local.env"), "utf8")).toContain(`ZYCODE_TEST_KEY=${KEY}`);
    expect(statSync(path.join(home, "local.env")).mode & 0o777).toBe(0o600);

    await controller.send("hello");
    await waitFor(() => controller.currentStatus === "ready", "first answer", 60_000);
    expect(replyText(controller)).toContain("SUB-ANSWER");
    expect(ofType(messages, "notice").filter((n) => n.actions?.some((a) => a.id === "runSetup"))).toEqual([]);

    // The key is in local.env only: not in the log of the extension, the chat, the state, the notices or any other file.
    const seen = JSON.stringify([host.log.lines, host.notifications, controller.transcript(), messages, controller.stateMessage(), host.setupRequests, terminal]);
    expect(seen).not.toContain(KEY);
    expect(allFilesText(home)).not.toContain(KEY);
  }, 180_000);

  it("a key that the provider refuses: the hint offers the setup, and a replaced key works", async () => {
    const port = await fakeModel(KEY);
    const terminal: string[] = [];
    const replace: Typed[] = [
      { prompt: "Replace the key in ZYCODE_TEST_KEY? [y/N]: ", answer: "y" },
      { prompt: "API key for ZYCODE_TEST_KEY (what you type is hidden): ", answer: KEY },
    ];
    let round = 0;
    const setup = (request: { command: string; args: string[]; env: Record<string, string> }) =>
      round++ === 0 ? setupTerminal(SETUP_FIRST(port, WRONG_KEY), terminal)(request) : setupTerminal(replace, terminal)(request);
    const { controller, host, home, messages } = start({ setup });
    await controller.start();
    await controller.runSetup();
    expect(controller.currentStatus).toBe("ready");

    await controller.send("hello");
    await waitFor(() => controller.currentStatus === "ready", "the refused turn", 60_000);
    const hint = ofType(messages, "notice").find((n) => n.actions?.some((a) => a.id === "runSetup"));
    expect(hint?.text).toMatch(/did not accept the API key/);

    await controller.runSetup();
    expect(host.setupRequests).toHaveLength(2);
    expect(readFileSync(path.join(home, "local.env"), "utf8")).toContain(`ZYCODE_TEST_KEY=${KEY}`);
    expect(readFileSync(path.join(home, "local.env"), "utf8")).not.toContain(WRONG_KEY);
    expect(controller.currentStatus).toBe("ready");
    await controller.send("hello again");
    await waitFor(() => controller.currentStatus === "ready", "the second turn", 60_000);
    expect(replyText(controller)).toContain("SUB-ANSWER");

    const seen = JSON.stringify([host.log.lines, host.notifications, controller.transcript(), messages, terminal]);
    expect(seen).not.toContain(KEY);
    expect(seen).not.toContain(WRONG_KEY);
  }, 240_000);

  it("a model that has no key value: needs-setup says which variable", async () => {
    const home = tempDir(dirs, "pihome-");
    writeFileSync(path.join(home, "agent.toml"), '[model]\nprovider = "openai"\nid = "m"\nbase_url = "http://127.0.0.1:9/v1"\napi_key_env = "ZYCODE_TEST_KEY"\n');
    const { controller } = start({ home });
    await controller.start();
    expect(controller.stateMessage().status).toBe("needs-setup");
    expect(controller.stateMessage().setup).toEqual({ reason: "no-key", variable: "ZYCODE_TEST_KEY" });
  }, 60_000);

  it("a wrong base URL is explained, with the config file in reach", async () => {
    const home = tempDir(dirs, "pihome-");
    writeFileSync(path.join(home, "agent.toml"), '[model]\nprovider = "openai"\nid = "m"\nbase_url = "http://127.0.0.1:9/v1"\napi_key_env = "ZYCODE_TEST_KEY"\n');
    writeFileSync(path.join(home, "local.env"), "ZYCODE_TEST_KEY=whatever\n");
    const { controller, messages } = start({ home });
    await controller.start();
    expect(controller.currentStatus).toBe("ready");
    await controller.send("hello");
    await waitFor(() => controller.currentStatus === "ready", "the failed turn", 90_000);
    const hint = ofType(messages, "notice").find((n) => n.actions?.some((a) => a.id === "openAgentConfig"));
    expect(hint?.text).toMatch(/could not reach|base URL/);
  }, 120_000);
});

describe.skipIf(!enabled)("session list with the real agent", () => {
  async function configuredHome(port: number): Promise<string> {
    const home = tempDir(dirs, "pihome-");
    writeFileSync(
      path.join(home, "agent.toml"),
      `[model]\nprovider = "openai"\nid = "m"\nbase_url = "http://127.0.0.1:${port}/v1"\napi_key_env = "ZYCODE_TEST_KEY"\n`,
    );
    writeFileSync(path.join(home, "local.env"), `ZYCODE_TEST_KEY=${KEY}\n`);
    return home;
  }

  it("lists, opens (history replays), and deletes; a new controller on the same home sees the same; another folder sees none", async () => {
    const port = await fakeModel(KEY);
    const home = await configuredHome(port);
    const one = start({ home });
    await one.controller.start();
    await one.controller.send("first topic");
    await waitFor(() => one.controller.currentStatus === "ready", "turn one", 60_000);
    const firstId = one.controller.currentSessionId!;
    await one.controller.newChat();
    await one.controller.refreshSessions();
    expect(rowsOf(one.controller).map((r) => r.title)).toEqual(["first topic"]);
    await one.controller.send("second topic");
    await waitFor(() => one.controller.currentStatus === "ready", "turn two", 60_000);
    await one.controller.refreshSessions();
    expect(rowsOf(one.controller).map((r) => r.title)).toEqual(["second topic", "first topic"]);
    expect(rowsOf(one.controller)[0]?.updatedAt).toBeDefined();

    await one.controller.openPastSession(firstId);
    expect(one.controller.currentSessionId).toBe(firstId);
    const history = one.controller.transcript();
    expect(history.find((i) => i.type === "user")).toMatchObject({ text: "first topic" });
    expect(replyText(one.controller)).toContain("SUB-ANSWER");
    expect(one.controller.currentStatus).toBe("ready");

    // The extension restarts: a new controller, the same home and folder.
    const folder = one.host.sessionFolder()!;
    await one.controller.dispose();
    const two = start({ home, folder });
    await two.controller.start();
    await two.controller.refreshSessions();
    expect(rowsOf(two.controller).map((r) => r.title)).toEqual(["second topic", "first topic"]);
    await two.controller.openPastSession(firstId);
    expect(two.controller.transcript().find((i) => i.type === "user")).toMatchObject({ text: "first topic" });

    // Delete the other chat: confirmed, gone from the list, and still gone after a restart.
    await two.controller.refreshSessions();
    const secondId = rowsOf(two.controller).find((r) => r.title === "second topic")!.id;
    await two.controller.deleteSession(secondId);
    await waitFor(() => two.controller.sessions.kind === "ready" && rowsOf(two.controller).length === 1, "the shorter list", 20_000);
    expect(two.host.confirmations.at(-1)?.message).toBe("Delete this chat?");
    await two.controller.dispose();
    const three = start({ home, folder });
    await three.controller.start();
    await three.controller.refreshSessions();
    expect(rowsOf(three.controller).map((r) => r.title)).toEqual(["first topic"]);
    await three.controller.dispose();

    // Another folder, the same home: none of the chats show, and the id cannot be opened.
    const four = start({ home });
    await four.controller.start();
    await four.controller.refreshSessions();
    expect(four.controller.sessions.kind).toBe("empty");
    await four.controller.openPastSession(firstId);
    expect(four.controller.currentSessionId).not.toBe(firstId);
    expect(four.controller.transcript().some((i) => i.type === "user" && i.text === "first topic")).toBe(false);
  }, 300_000);

  it("a past chat is not opened in always-approve", async () => {
    const port = await fakeModel(KEY);
    const home = await configuredHome(port);
    const run = start({ home });
    await run.controller.start();
    await run.controller.send("topic");
    await waitFor(() => run.controller.currentStatus === "ready", "turn", 60_000);
    const id = run.controller.currentSessionId!;
    await run.controller.newChat();
    await run.controller.applyPermissionMode("always-approve");
    expect(run.controller.stateMessage().mode?.current).toBe("always-approve");
    await run.controller.refreshSessions();
    await run.controller.openPastSession(id);
    expect(run.controller.stateMessage().mode?.current).toBe("ask");
    await run.controller.newChat();
    expect(run.controller.stateMessage().mode?.current).toBe("ask");
  }, 120_000);
});
