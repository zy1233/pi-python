import { writeFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import { TestHost, newController, ofType, tempDir, waitFor } from "./helpers.js";

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

const marker = () => path.join(tempDir(dirs, "marker-"), "configured");

describe("first run: the agent has no model", () => {
  it("shows needs-setup with the reason, and offers the setup", async () => {
    withEnv({ FAKE_AUTH_MARKER: marker() });
    const { controller } = setup();
    await controller.start();
    const state = controller.stateMessage();
    expect(state.status).toBe("needs-setup");
    expect(state.setup).toEqual({ reason: "no-model" });
    expect(state.mode).toBeUndefined();
  });

  it("reads the name of a key variable with no value", async () => {
    withEnv({ FAKE_AUTH_MARKER: marker(), FAKE_AUTH_MESSAGE: "The variable DEEPSEEK_API_KEY has no value. Run the setup." });
    const { controller } = setup();
    await controller.start();
    expect(controller.stateMessage().setup).toEqual({ reason: "no-key", variable: "DEEPSEEK_API_KEY" });
  });

  it("runs the setup in the terminal of the agent, in the folder, and then chats", async () => {
    const file = marker();
    withEnv({ FAKE_AUTH_MARKER: file });
    const { controller, host } = setup({
      setup: async () => {
        writeFileSync(file, "ok");
        return { exitCode: 0 };
      },
    });
    await controller.start();
    await controller.runSetup();
    expect(controller.currentStatus).toBe("ready");
    expect(controller.stateMessage().setup).toBeUndefined();
    await controller.send("hello");
    await waitFor(() => controller.currentStatus === "ready", "turn");
    expect(controller.transcript().some((i) => i.type === "agent" && i.text === "Hello")).toBe(true);
    expect(host.setupRequests[0]?.args).toEqual([expect.stringContaining("fake-agent"), "--setup"]);
  });

  it("can run the setup again later from a ready chat, to change the key", async () => {
    const { controller, host } = setup({ setup: async () => ({ exitCode: 0 }) });
    await controller.start();
    expect(controller.currentStatus).toBe("ready");
    await controller.runSetup();
    expect(host.setupRequests).toHaveLength(1);
    expect(controller.currentStatus).toBe("ready");
  });

  it("does not start a second setup while one runs", async () => {
    let release: (() => void) | undefined;
    const { controller, host } = setup({
      setup: () => new Promise((resolve) => (release = () => resolve({ exitCode: 1 }))),
    });
    await controller.start();
    const first = controller.runSetup();
    await waitFor(() => host.setupRequests.length === 1, "terminal");
    await controller.runSetup();
    expect(host.setupRequests).toHaveLength(1);
    release?.();
    await first;
  });
});

describe("first run: failures that come with the first message", () => {
  const cases: [string, string, string][] = [
    ["a key that the provider refuses", "error:key", "runSetup"],
    ["a model id that does not exist", "error:model", "openAgentConfig"],
    ["a server that cannot be reached", "error:conn", "openLog"],
  ];
  for (const [name, prompt, action] of cases) {
    it(`explains ${name}, with a button`, async () => {
      const { controller, messages } = setup();
      await controller.start();
      await controller.send(prompt);
      await waitFor(() => controller.currentStatus === "ready", "turn");
      const hint = ofType(messages, "notice").find((n) => n.actions?.some((a) => a.id === action));
      expect(hint, "a hint with the button").toBeDefined();
      expect(hint?.text).not.toMatch(/sk-fake/);
    });
  }

  it("says nothing for a normal answer that talks about keys", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send("What is an API key? Error: none");
    await waitFor(() => controller.currentStatus === "ready", "turn");
    expect(ofType(messages, "notice").filter((n) => n.actions !== undefined)).toEqual([]);
  });
});

describe("secrets", () => {
  it("never go to the log, the transcript or the state", async () => {
    const secret = "sk-test-SECRET-1234567890";
    const file = marker();
    withEnv({ FAKE_AUTH_MARKER: file });
    const { controller, host, messages } = setup({
      setup: async () => {
        writeFileSync(file, "ok");
        return { exitCode: 0 };
      },
    });
    await controller.start();
    await controller.runSetup();
    await controller.send("error:key");
    await waitFor(() => controller.currentStatus === "ready", "turn");
    const everything = JSON.stringify([host.log.lines, host.notifications, controller.transcript(), messages, controller.stateMessage(), host.setupRequests]);
    expect(everything).not.toContain(secret);
    expect(host.setupRequests[0]?.env).not.toHaveProperty("OPENAI_API_KEY");
  });
});
