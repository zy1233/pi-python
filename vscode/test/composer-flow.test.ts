import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { MentionCandidate } from "../src/core/mentions.js";
import { TestHost, newController, ofType, readLog, tempDir, waitFor } from "./helpers.js";

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

describe("available_commands_update and model switching", () => {
  it("populates stateMessage().commands from available_commands_update and clears on newChat and stop", async () => {
    restoreEnv = withEnv({
      FAKE_COMMANDS: JSON.stringify([
        { name: "goal", description: "Goal planning", input: { hint: "<desc>" } },
        { name: "auto-log", description: "Show Auto decisions", input: { hint: "<args>" } },
      ]),
    });
    const { controller, messages } = setup();
    await controller.start();

    await waitFor(
      () => (controller.stateMessage().commands?.length ?? 0) === 2,
      "available_commands_update to populate state.commands",
    );
    expect(controller.stateMessage().commands).toEqual([
      { name: "goal", description: "Goal planning", inputHint: "<desc>" },
      { name: "auto-log", description: "Show Auto decisions", inputHint: "<args>" },
    ]);
    expect(ofType(messages, "state").some((s) => s.commands?.some((c) => c.name === "goal"))).toBe(true);

    // Opening a new chat on the same agent re-receives commands; restarting picks up the new agent's commands.
    await controller.newChat();
    await waitFor(
      () => (controller.stateMessage().commands?.length ?? 0) === 2,
      "commands update after newChat",
    );

    restoreEnv();
    restoreEnv = withEnv({ FAKE_COMMANDS: JSON.stringify([{ name: "mcp", description: "MCP servers" }]) });
    await controller.restart();
    await waitFor(
      () => controller.stateMessage().commands?.[0]?.name === "mcp",
      "commands update after restart",
    );

    await controller.stop();
    expect(controller.stateMessage().commands).toBeUndefined();
    expect(controller.stateMessage().models).toBeUndefined();
  });

  it("switches model via session/set_config_option and updates state", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    restoreEnv = withEnv({
      FAKE_LOG: log,
      FAKE_MODELS: JSON.stringify([
        { value: "gpt-4o", name: "GPT-4o" },
        { value: "deepseek-chat", name: "DeepSeek Chat" },
      ]),
    });
    const { controller, messages } = setup();
    await controller.start();

    expect(controller.stateMessage().models).toEqual({
      current: "gpt-4o",
      options: [
        { id: "gpt-4o", name: "GPT-4o" },
        { id: "deepseek-chat", name: "DeepSeek Chat" },
      ],
    });
    expect(controller.stateMessage().model).toBe("GPT-4o");

    await controller.applyModel("deepseek-chat");
    expect(controller.stateMessage().model).toBe("DeepSeek Chat");
    expect(controller.stateMessage().models?.current).toBe("deepseek-chat");
    expect(ofType(messages, "notice").some((n) => n.text === "Model: DeepSeek Chat.")).toBe(true);
    expect(readLog(log)).toContain("session/set_config_option model=deepseek-chat");
  });
});

describe("@ mentions in ChatController", () => {
  it("avoids duplicating inline @path tokens and prepends detached chips", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    restoreEnv = withEnv({ FAKE_LOG: log });
    const { controller, messages } = setup();
    await controller.start();

    await controller.send("inspect @src/app.ts", [{ kind: "file", path: "src/app.ts" }]);
    await controller.send("explain this", [{ kind: "file", path: "src/other.ts" }]);

    const userTexts = ofType(messages, "userMessage").map((m) => m.text);
    expect(userTexts).toEqual([
      "inspect @src/app.ts",
      "Referenced context: @src/other.ts\n\nexplain this",
    ]);
    const wireLog = readFileSync(log, "utf8");
    expect(wireLog).toContain("session/prompt inspect @src/app.ts");
    expect(wireLog).toContain("session/prompt Referenced context: @src/other.ts\n\nexplain this");
  });

  it("returns ranked host results only when trusted and a folder is open", async () => {
    const candidates: MentionCandidate[] = [
      { kind: "file", path: "src/app.ts", label: "app.ts", detail: "src" },
      { kind: "file", path: "README.md", label: "README.md", detail: "" },
    ];

    const trusted = setup({ mentions: candidates });
    await trusted.controller.start();
    const hits = await trusted.controller.searchMentions("app");
    expect(hits.map((h) => h.path)).toEqual(["src/app.ts"]);

    const untrusted = setup({ trusted: false, mentions: candidates });
    await untrusted.controller.start();
    expect(await untrusted.controller.searchMentions("app")).toEqual([]);

    const noFolder = setup({ folder: null, mentions: candidates });
    await noFolder.controller.start();
    expect(await noFolder.controller.searchMentions("app")).toEqual([]);
  });
});
