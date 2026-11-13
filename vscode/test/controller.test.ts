import { writeFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { HostMessage } from "../src/core/protocol.js";
import { FAKE_AGENT, TestHost, newController, ofType, readLog, tempDir, waitFor } from "./helpers.js";

const hosts: TestHost[] = [];
const controllers: ChatController[] = [];
const dirs: string[] = [];

function setup(options: ConstructorParameters<typeof TestHost>[0] = {}) {
  const host = new TestHost(options);
  hosts.push(host);
  const made = newController(host);
  controllers.push(made.controller);
  return { host, ...made };
}

function withEnv(values: Record<string, string>): () => void {
  const saved: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(values)) {
    saved[key] = process.env[key];
    process.env[key] = value;
  }
  return () => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

let restoreEnv: (() => void) | undefined;

afterEach(async () => {
  restoreEnv?.();
  restoreEnv = undefined;
  await Promise.all(controllers.splice(0).map((c) => c.dispose()));
  for (const host of hosts.splice(0)) host.cleanup();
  dirs.length = 0;
});

const agentText = (messages: HostMessage[]) =>
  ofType(messages, "agentText")
    .map((m) => m.text)
    .join("");

describe("start", () => {
  it("opens a session and reports ready with the agent version and the mode", async () => {
    const { controller, host } = setup();
    await controller.start();
    const state = controller.stateMessage();
    expect(state.status).toBe("ready");
    expect(state.agent).toEqual({ version: "0.8.0", source: "setting" });
    expect(state.cwd).toBe(host.sessionFolder());
    expect(state.mode).toEqual({
      current: "ask",
      options: [
        { id: "ask", name: "Ask" },
        { id: "auto", name: "Auto" },
        { id: "always-approve", name: "Always approve" },
      ],
    });
    expect(state.model).toBe("Fake model");
  });

  it("does not start in an untrusted workspace", async () => {
    const { controller } = setup({ trusted: false });
    await controller.start();
    expect(controller.currentStatus).toBe("untrusted");
  });

  it("asks for a folder when none is open", async () => {
    const { controller } = setup({ folder: null });
    await controller.start();
    expect(controller.currentStatus).toBe("no-folder");
  });

  it("reports a program that does not exist", async () => {
    const { controller } = setup({ settings: { agentPath: path.join(tempDir(dirs, "x-"), "nothing"), agentArgs: [] } });
    await controller.start();
    const state = controller.stateMessage();
    expect(state.status).toBe("error");
    expect(state.detail).toContain("not found");
  });

  it("explains a missing bundled binary", async () => {
    const { controller } = setup({ settings: { agentPath: "", agentArgs: [] } });
    await controller.start();
    const state = controller.stateMessage();
    expect(state.status).toBe("unavailable");
    expect(state.detail).toContain("zycode.agentPath");
  });

  it("fails a program that is not the zycode agent", async () => {
    restoreEnv = withEnv({ FAKE_AGENT_NAME: "other-agent" });
    const { controller } = setup();
    await controller.start();
    expect(controller.currentStatus).toBe("error");
    expect(controller.stateMessage().detail).toContain("does not speak");
  });

  it("shows the stderr tail when the agent dies at start", async () => {
    restoreEnv = withEnv({ FAKE_EXIT_AT_START: "2" });
    const { controller } = setup();
    await controller.start();
    const state = controller.stateMessage();
    expect(state.status).toBe("error");
    expect(state.detail).toContain("exit code 2");
    expect(state.detail).toContain("cannot start");
  });

  it("has no mode switch when the agent has no mode option, and says so for Auto", async () => {
    restoreEnv = withEnv({ FAKE_NO_MODE: "1" });
    const { controller, messages } = setup({ settings: { permissionMode: "auto" } });
    await controller.start();
    expect(controller.stateMessage().mode).toBeUndefined();
    expect(ofType(messages, "notice").some((n) => n.text.includes("no Auto mode"))).toBe(true);
  });

  it("applies the permission setting through session/set_config_option", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    restoreEnv = withEnv({ FAKE_LOG: log });
    const { controller } = setup({ settings: { permissionMode: "auto" } });
    await controller.start();
    expect(controller.stateMessage().mode?.current).toBe("auto");
    expect(readLog(log)).toContain("session/set_config_option mode=auto");
  });

  it("declares the terminal login and nothing else", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    restoreEnv = withEnv({ FAKE_LOG: log });
    const { controller } = setup();
    await controller.start();
    const line = readLog(log).find((l) => l.startsWith("initialize "));
    const capabilities = JSON.parse(line!.slice("initialize ".length));
    expect(capabilities.auth).toEqual({ terminal: true });
    expect(capabilities.terminal).toBeFalsy();
    expect(capabilities.fs?.readTextFile).toBeFalsy();
    expect(capabilities.fs?.writeTextFile).toBeFalsy();
  });
});

describe("chat", () => {
  it("streams text and thoughts into one message, and ends the turn", async () => {
    const { controller, messages } = setup();
    await controller.start();
    expect(await controller.send("hello")).toBe(true);
    expect(agentText(messages)).toBe("Hello");
    expect(ofType(messages, "agentThought").map((m) => m.text).join("")).toBe("Thinking. ");
    expect(ofType(messages, "turnEnd")).toEqual([{ type: "turnEnd", stopReason: "end_turn" }]);
    expect(controller.currentStatus).toBe("ready");
    const transcript = controller.transcript();
    expect(transcript.map((i) => i.type)).toEqual(["user", "agent"]);
    expect(transcript[1]).toMatchObject({ type: "agent", text: "Hello", thought: "Thinking. " });
  });

  it("does not send an empty prompt or a prompt when the agent is not ready", async () => {
    const { controller } = setup();
    expect(await controller.send("hi")).toBe(false);
    await controller.start();
    expect(await controller.send("   ")).toBe(false);
  });

  it("merges tool output and the exit code for bash, with no permission question", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send("tool:bash");
    const calls = ofType(messages, "toolCall");
    const last = calls[calls.length - 1]!.toolCall;
    expect(last.status).toBe("failed");
    expect(last.terminal?.exitCode).toBe(3);
    expect(last.terminal?.output).toContain("hi");
    expect(ofType(messages, "permission")).toHaveLength(0);
  });

  it("asks for a write, and sends the choice that the user made", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("tool:write");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card");
    const card = ofType(messages, "permission")[0]!;
    expect(card).toMatchObject({ kind: "tool", title: "write a.txt", summary: "a.txt" });
    expect(card.options.map((o) => o.id)).toEqual(["allow-once", "reject-once"]);
    expect(controller.answerPermission(card.requestId, "allow-once")).toBe(true);
    await turn;
    expect(agentText(messages)).toBe("written");
    expect(ofType(messages, "permissionDone")[0]).toMatchObject({ outcome: "selected", optionId: "allow-once" });
  });

  it("refuses an option that the request did not list, and a second answer", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("tool:write");
    await waitFor(() => ofType(messages, "permission").length === 1, "the permission card");
    const { requestId } = ofType(messages, "permission")[0]!;
    expect(controller.answerPermission(requestId, "allow-always")).toBe(false);
    expect(controller.answerPermission("nope", "allow-once")).toBe(false);
    expect(controller.answerPermission(requestId, "reject-once")).toBe(true);
    expect(controller.answerPermission(requestId, "allow-once")).toBe(false);
    await turn;
    expect(agentText(messages)).toBe("refused");
  });

  it("shows the trust question with its own text and the two options of the agent", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("trust");
    await waitFor(() => ofType(messages, "permission").length === 1, "the trust card");
    const card = ofType(messages, "permission")[0]!;
    expect(card.kind).toBe("trust");
    expect(card.summary).toContain("not the VS Code Workspace Trust");
    controller.answerPermission(card.requestId, "dont-trust-project");
    await turn;
    expect(agentText(messages)).toContain("trust:dont-trust-project");
  });

  it("does not treat a trust-looking question with other options as a trust question", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("trust:odd");
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    expect(ofType(messages, "permission")[0]!.kind).toBe("tool");
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "reject-once");
    await turn;
  });

  it("cancels a turn and answers the open question as cancelled", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("hang");
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    await controller.cancel();
    expect(controller.currentStatus).toBe("cancelling");
    await turn;
    expect(controller.currentStatus).toBe("ready");
    expect(ofType(messages, "permissionDone")[0]).toMatchObject({ outcome: "cancelled" });
    expect(ofType(messages, "turnEnd").at(-1)).toEqual({ type: "turnEnd", stopReason: "cancelled" });
  });

  it("notes unsaved files", async () => {
    const { controller, messages } = setup({ dirty: ["/p/a.ts", "/p/b.ts"] });
    await controller.start();
    await controller.send("hello");
    expect(ofType(messages, "notice").some((n) => n.text.startsWith("2 files have unsaved changes"))).toBe(true);
  });

  it("shows a refusal as a notice", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send("refuse");
    expect(ofType(messages, "notice").some((n) => n.text.includes("refusal"))).toBe(true);
  });

  it("goes to error when the agent dies in a turn, with the stderr tail", async () => {
    const { controller, messages, host } = setup();
    await controller.start();
    await controller.send("crash");
    await waitFor(() => controller.currentStatus === "error", "the error state");
    const state = controller.stateMessage();
    expect(state.detail).toContain("exit code 3");
    expect(state.detail).toContain("crashing");
    expect(ofType(messages, "permissionDone")).toHaveLength(0);
    expect(host.log.lines.some((l) => l.startsWith("error The agent exited"))).toBe(true);
  });

  it("starts again after a crash, with an empty chat only on request", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send("crash");
    await waitFor(() => controller.currentStatus === "error", "the error state");
    await controller.restart(true);
    expect(controller.currentStatus).toBe("ready");
    expect(controller.transcript().length).toBeGreaterThan(0);
    await controller.restart();
    expect(controller.transcript()).toEqual([]);
    expect(ofType(messages, "reset").length).toBeGreaterThan(0);
  });

  it("a new chat closes the old session and opens another", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    restoreEnv = withEnv({ FAKE_LOG: log });
    const { controller } = setup();
    await controller.start();
    await controller.send("hello");
    await controller.newChat();
    expect(controller.transcript()).toEqual([]);
    expect(controller.currentStatus).toBe("ready");
    const lines = readLog(log);
    expect(lines).toContain("session/close fake-1");
    expect(lines.filter((l) => l.startsWith("session/new "))).toHaveLength(2);
  });
});

describe("setup", () => {
  it("goes to needs-setup, runs the terminal, and starts again", async () => {
    const marker = path.join(tempDir(dirs, "marker-"), "configured");
    restoreEnv = withEnv({ FAKE_AUTH_MARKER: marker });
    const { controller, host } = setup({
      setup: async () => {
        writeFileSync(marker, "ok");
        return { exitCode: 0 };
      },
    });
    await controller.start();
    expect(controller.currentStatus).toBe("needs-setup");
    await controller.runSetup();
    expect(host.setupRequests).toHaveLength(1);
    expect(host.setupRequests[0]).toMatchObject({
      command: process.execPath,
      args: [FAKE_AGENT, "--setup"],
      env: { FAKE_SETUP: "1" },
    });
    expect(controller.currentStatus).toBe("ready");
  });

  it("stays in needs-setup and says so when the terminal does not finish", async () => {
    const marker = path.join(tempDir(dirs, "marker-"), "configured");
    restoreEnv = withEnv({ FAKE_AUTH_MARKER: marker });
    const { controller, messages } = setup({ setup: async () => ({ exitCode: 1 }) });
    await controller.start();
    await controller.runSetup();
    expect(controller.currentStatus).toBe("needs-setup");
    expect(ofType(messages, "notice").some((n) => n.text.startsWith("Setup did not finish."))).toBe(true);
  });
});

describe("stop", () => {
  it("stops the agent and ends in stopped", async () => {
    const { controller } = setup();
    await controller.start();
    await controller.stop();
    expect(controller.currentStatus).toBe("stopped");
  });

  it("stops during a turn without an error", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("hang");
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    await controller.stop();
    await turn;
    expect(controller.currentStatus).toBe("stopped");
    expect(ofType(messages, "notice").filter((n) => n.level === "error")).toEqual([]);
  });
});
