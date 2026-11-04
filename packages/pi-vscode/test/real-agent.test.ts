// Runs the controller against the real Python agent (the scripted `_tool_agent.py` of pi-agent-cli,
// which needs no model). Off by default: `npm run test:agent`, or set ZYCODE_REAL_AGENT_PYTHON.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { TestHost, newController, ofType, tempDir, waitFor } from "./helpers.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const python = process.env.ZYCODE_REAL_AGENT_PYTHON ?? path.join(repo, ".venv", "bin", "python");
const script = path.join(repo, "packages", "pi-agent-cli", "tests", "_tool_agent.py");
const enabled = process.env.ZYCODE_REAL_AGENT === "1" && existsSync(python) && existsSync(script);

const dirs: string[] = [];
const saved: Record<string, string | undefined> = {};
const cleanups: (() => Promise<void> | void)[] = [];

function setEnv(values: Record<string, string>): void {
  for (const [key, value] of Object.entries(values)) {
    if (!(key in saved)) saved[key] = process.env[key];
    process.env[key] = value;
  }
}

afterEach(async () => {
  for (const cleanup of cleanups.splice(0)) await cleanup();
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

function start(toolCall: Record<string, unknown>, permission: string) {
  setEnv({
    PI_HOME: tempDir(dirs, "pihome-"),
    PI_USE_MOCK: "1",
    PI_TEST_PERMISSION: permission,
    PI_TEST_TOOL_CALL: JSON.stringify(toolCall),
  });
  const host = new TestHost({ settings: { agentPath: python, agentArgs: [script] } });
  const made = newController(host);
  cleanups.push(() => made.controller.dispose(), () => host.cleanup());
  return { host, ...made };
}

describe.skipIf(!enabled)("real agent", () => {
  it("opens a session and reports the agent identity", async () => {
    const { controller } = start({ name: "write", arguments: { path: "x.txt", content: "x" } }, "ask");
    await controller.start();
    expect(controller.currentStatus).toBe("ready");
    expect(controller.stateMessage().agent?.version).toMatch(/^\d+\.\d+\.\d+/);
  }, 60_000);

  it("shows the permission question of a write and writes after the user allows it", async () => {
    const { controller, messages, host } = start(
      { id: "call_w", name: "write", arguments: { path: "a.txt", content: "hello\n" } },
      "ask",
    );
    await controller.start();
    const turn = controller.send("go");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card", 30_000);
    const card = ofType(messages, "permission")[0]!;
    expect(card).toMatchObject({ kind: "tool", title: "write a.txt", summary: "a.txt" });
    controller.answerPermission(card.requestId, "allow-once");
    await turn;
    const file = path.join(host.sessionFolder()!, "a.txt");
    expect(readFileSync(file, "utf8")).toBe("hello\n");
    const calls = ofType(messages, "toolCall");
    expect(calls.at(-1)?.toolCall.status).toBe("completed");
  }, 60_000);

  it("does not write when the user refuses", async () => {
    const { controller, messages, host } = start(
      { id: "call_w", name: "write", arguments: { path: "b.txt", content: "no\n" } },
      "ask",
    );
    await controller.start();
    const turn = controller.send("go");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card", 30_000);
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "reject-once");
    await turn;
    expect(existsSync(path.join(host.sessionFolder()!, "b.txt"))).toBe(false);
  }, 60_000);

  it("asks for bash even if the agent is set to always-approve, then shows output and exit code", async () => {
    const { controller, messages } = start(
      { id: "call_b", name: "bash", arguments: { command: "echo hi; exit 3" } },
      "always-approve",
    );
    await controller.start();
    expect(controller.stateMessage().mode?.current).toBe("ask");
    const turn = controller.send("go");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card", 30_000);
    const card = ofType(messages, "permission")[0]!;
    expect(card.summary).toBe("echo hi; exit 3");
    controller.answerPermission(card.requestId, "allow-once");
    await turn;
    const last = ofType(messages, "toolCall").at(-1)!.toolCall;
    expect(last.terminal?.output).toContain("hi");
    expect(last.terminal?.exitCode).toBe(3);
  }, 60_000);

  it("builds the diff of a real edit from the patch the agent sends", async () => {
    const { controller, messages, host } = start(
      { id: "call_e", name: "edit", arguments: { path: "e.txt", edits: [{ oldText: "line 3\n", newText: "line three\nextra\n" }] } },
      "ask",
    );
    const file = path.join(host.sessionFolder()!, "e.txt");
    const original = Array.from({ length: 12 }, (_, i) => `line ${i + 1}`).join("\n") + "\n";
    writeFileSync(file, original);
    await controller.start();
    const turn = controller.send("go");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card", 30_000);
    const card = ofType(messages, "permission")[0]!;
    expect(card.canDiff).toBe(true);
    const proposed = controller.diffFor("call_e")!;
    expect(controller.diffStore.get(proposed.key, "before")).toBe(original);
    expect(controller.diffStore.get(proposed.key, "after")).toContain("line three\nextra\n");
    controller.answerPermission(card.requestId, "allow-once");
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.canDiff === true, "the diff flag", 10_000);
    const result = controller.diffFor("call_e")!;
    expect(result.title).toBe("e.txt (edited)");
    expect(result.note).toBeUndefined();
    expect(controller.diffStore.get(result.key, "before")).toBe(original);
    expect(controller.diffStore.get(result.key, "after")).toBe(readFileSync(file, "utf8"));
  }, 60_000);

  it("builds the diff of a real write over an existing file", async () => {
    const { controller, messages, host } = start(
      { id: "call_w", name: "write", arguments: { path: "w.txt", content: "new\n" } },
      "ask",
    );
    writeFileSync(path.join(host.sessionFolder()!, "w.txt"), "old\n");
    await controller.start();
    const turn = controller.send("go");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card", 30_000);
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "allow-once");
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.canDiff === true, "the diff flag", 10_000);
    const result = controller.diffFor("call_w")!;
    expect(controller.diffStore.get(result.key, "before")).toBe("old\n");
    expect(controller.diffStore.get(result.key, "after")).toBe("new\n");
  }, 60_000);

  it("stops the process on stop()", async () => {
    const { controller } = start({ name: "write", arguments: { path: "x.txt", content: "x" } }, "ask");
    await controller.start();
    await controller.stop();
    expect(controller.currentStatus).toBe("stopped");
  }, 60_000);
});
