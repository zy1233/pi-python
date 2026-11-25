// The real agent and a fake model server on 127.0.0.1: the model calls `workflow` and `codemode`,
// and the permission cards must show the whole script of each call. Off by default:
// ZYCODE_REAL_AGENT=1. It needs agent 1.1.0, which has `codemode`: set ZYCODE_REAL_AGENT_BINARY to
// that binary (for example `.vsix-cache/v1.1.0/` after `npm run package linux-x64`), or use a
// ZYCODE_REAL_AGENT_PYTHON in which `pi-codemode-py` is installed.
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { TestHost, newController, ofType, tempDir, waitFor } from "./helpers.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const python = process.env.ZYCODE_REAL_AGENT_PYTHON ?? path.join(repo, ".venv", "bin", "python");
const binary = process.env.ZYCODE_REAL_AGENT_BINARY;
const enabled = process.env.ZYCODE_REAL_AGENT === "1" && (binary !== undefined || existsSync(python)) && process.platform !== "win32";

// The scripts that the fake model sends when the prompt has RUN-SMOKE (`SCRIPT` and `CODEMODE_CODE`
// of packaging/agent/smoke.py).
const WORKFLOW_SCRIPT = '\nasync def main():\n    answer = await agent("SUB-PROMPT")\n    result({"answer": answer})\n';
const CODEMODE_CODE = 'listing = await tools.ls(path=".")\nreturn f"CM-OK:{len(listing) > 0}"';

const dirs: string[] = [];
const cleanups: (() => Promise<void> | void)[] = [];
const saved: Record<string, string | undefined> = {};

afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup();
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
    delete saved[key];
  }
});

function setEnv(values: Record<string, string | undefined>): void {
  for (const [key, value] of Object.entries(values)) {
    if (!(key in saved)) saved[key] = process.env[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

async function fakeModel(): Promise<number> {
  const child: ChildProcess = spawn(python, [path.join(here, "support", "fake_model.py")], { stdio: ["ignore", "pipe", "inherit"] });
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

describe.skipIf(!enabled)("permission cards of codemode and workflow with the real agent", () => {
  it("shows the whole script of each call before the user decides", async () => {
    const dir = tempDir(dirs, "codemode-");
    const home = path.join(dir, "home");
    mkdirSync(home);
    const port = await fakeModel();
    writeFileSync(
      path.join(home, "agent.toml"),
      `[model]\nprovider = "openai"\nid = "smoke-model"\nbase_url = "http://127.0.0.1:${port}/v1"\napi_key_env = "ZYCODE_TEST_KEY"\n`,
    );
    setEnv({
      PI_HOME: home,
      ZYCODE_TEST_KEY: "sk-codemode-0123456789",
      PI_USE_MOCK: undefined,
      PI_TEST_PERMISSION: undefined,
      PI_TEST_TOOL_CALL: undefined,
      OPENAI_API_KEY: undefined,
    });
    const host = new TestHost({
      settings: binary ? { agentPath: binary, agentArgs: [] } : { agentPath: python, agentArgs: ["-m", "pi_agent_cli"] },
      agentConfig: path.join(home, "agent.toml"),
      confirm: true,
    });
    const { controller, messages } = newController(host);
    cleanups.push(() => controller.dispose(), () => host.cleanup());
    await controller.start();
    expect(controller.currentStatus).toBe("ready");
    expect(controller.stateMessage().mode?.current).toBe("ask");

    let finished = false;
    const turn = controller.send("RUN-SMOKE").finally(() => {
      finished = true;
    });
    const answered = new Set<string>();
    const cards = new Map<string, { title: string; summary: string }>();
    // The title of a codemode card is `codemode (N lines)`, so the key is its first word.
    const keyOf = (title: string) => title.split(" ")[0]!;
    const deadline = Date.now() + 120_000;
    while (!finished && Date.now() < deadline) {
      for (const card of ofType(messages, "permission")) {
        if (answered.has(card.requestId)) continue;
        answered.add(card.requestId);
        cards.set(keyOf(card.title), { title: card.title, summary: card.summary });
        controller.answerPermission(card.requestId, "reject-once");
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    await turn;
    await waitFor(() => controller.currentStatus === "ready", "end of turn", 60_000);

    expect(cards.get("workflow")?.summary).toBe(WORKFLOW_SCRIPT);
    expect(cards.get("codemode")?.summary).toBe(CODEMODE_CODE);
  }, 240_000);
});
