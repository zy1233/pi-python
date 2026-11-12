// The real Python agent and a fake model server on 127.0.0.1 that logs each request body. It shows
// what the agent asks the provider for at each thinking level. Off by default: ZYCODE_REAL_AGENT=1
// (`npm run test:agent`).
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import { TestHost, newController, tempDir, waitFor } from "./helpers.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const python = process.env.ZYCODE_REAL_AGENT_PYTHON ?? path.join(repo, ".venv", "bin", "python");
const enabled = process.env.ZYCODE_REAL_AGENT === "1" && existsSync(python) && process.platform !== "win32";

/** A built agent program (for example the 0.9.0 binary) instead of the Python module. */
const binary = process.env.ZYCODE_REAL_AGENT_BINARY;

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

async function fakeModel(log: string): Promise<number> {
  const child: ChildProcess = spawn(python, [path.join(here, "support", "fake_model.py"), "--log", log], { stdio: ["ignore", "pipe", "inherit"] });
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

type Body = Record<string, unknown>;
const requests = (log: string): Body[] =>
  existsSync(log)
    ? readFileSync(log, "utf8")
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line) as Body)
    : [];

/** The parameters of a request that ask the provider to think. */
function thinkingParams(body: Body): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ["reasoning_effort", "reasoning", "thinking", "extra_body", "enable_thinking"]) {
    if (key in body) out[key] = body[key];
  }
  return out;
}

async function rig(toml: (port: number) => string) {
  const dir = tempDir(dirs, "think-");
  const log = path.join(dir, "model.log");
  const home = path.join(dir, "home");
  const port = await fakeModel(log);
  mkdirSync(home);
  writeFileSync(path.join(home, "agent.toml"), toml(port));
  setEnv({
    PI_HOME: home,
    ZYCODE_TEST_KEY: "sk-think-0123456789",
    PI_USE_MOCK: undefined,
    PI_TEST_PERMISSION: undefined,
    PI_TEST_TOOL_CALL: undefined,
    OPENAI_API_KEY: undefined,
    DEEPSEEK_API_KEY: undefined,
    ANTHROPIC_API_KEY: undefined,
  });
  const make = () => {
    const host = new TestHost({
      settings: binary ? { agentPath: binary, agentArgs: [] } : { agentPath: python, agentArgs: ["-m", "pi_agent_cli"] },
      agentConfig: path.join(home, "agent.toml"),
      confirm: true,
    });
    const made = newController(host);
    cleanups.push(() => made.controller.dispose(), () => host.cleanup());
    return made.controller;
  };
  return { log, home, file: path.join(home, "agent.toml"), make };
}

const MODEL = (port: number, extra = "") =>
  `${extra}[model]\nprovider = "openai"\nid = "think-model"\nbase_url = "http://127.0.0.1:${port}/v1"\napi_key_env = "ZYCODE_TEST_KEY"\n`;

async function say(controller: ChatController, text: string): Promise<void> {
  await controller.send(text);
  await waitFor(() => controller.currentStatus === "ready", "end of turn", 60_000);
}

describe.skipIf(!enabled)("the thinking level with the real agent", () => {
  it("shows in the model request: a level, a change, the same chat reopened, a restart", async () => {
    const { log, file, make } = await rig((port) => MODEL(port, 'thinking_level = "low"\n\n'));
    const first = make();
    await first.start();
    expect(first.stateMessage().thinking).toMatchObject({ current: "low" });
    await say(first, "first question");
    const one = requests(log);
    expect(one).toHaveLength(1);
    expect(thinkingParams(one[0]!)).toMatchObject({ reasoning_effort: "low" });

    await first.applyThinkingLevel("high");
    expect(first.currentStatus).toBe("ready");
    expect(first.stateMessage().thinking).toMatchObject({ current: "high" });
    expect(readFileSync(file, "utf8")).toMatch(/^thinking_level = "high"\n\n\[model\]/);
    // The chat is the same: the agent replayed it, and the model gets the earlier question again.
    expect(first.transcript().some((item) => item.type === "user" && item.text === "first question")).toBe(true);
    await say(first, "second question");
    const two = requests(log);
    expect(two).toHaveLength(2);
    expect(thinkingParams(two[1]!)).toMatchObject({ reasoning_effort: "high" });
    expect(JSON.stringify(two[1]!.messages)).toContain("first question");

    await first.dispose();
    const second = make();
    await second.start();
    expect(second.stateMessage().thinking).toMatchObject({ current: "high" });
    await say(second, "third question");
    expect(thinkingParams(requests(log)[2]!)).toMatchObject({ reasoning_effort: "high" });
  });

  it("asks the provider for no thinking at off", async () => {
    const { log, make } = await rig((port) => MODEL(port, 'thinking_level = "high"\n\n'));
    const controller = make();
    await controller.start();
    await controller.applyThinkingLevel("off");
    expect(controller.stateMessage().thinking).toMatchObject({ current: "off" });
    await say(controller, "question");
    expect(thinkingParams(requests(log).at(-1)!)).toEqual({});
  });

  it("has no picker for a model with reasoning = false, and the agent asks for no thinking", async () => {
    const { log, file, make } = await rig((port) => `thinking_level = "high"\n\n${MODEL(port)}reasoning = false\n`);
    const controller = make();
    await controller.start();
    const thinking = controller.stateMessage().thinking;
    expect(thinking?.disabled).toMatch(/reasoning = false/);
    await controller.applyThinkingLevel("low");
    expect(readFileSync(file, "utf8")).toContain('thinking_level = "high"');
    await say(controller, "question");
    expect(thinkingParams(requests(log).at(-1)!)).toEqual({});
  });
});
