import { describe, expect, it } from "vitest";

import {
  buildCommandCatalog,
  canRunClientCommandInStatus,
  detectComposerTrigger,
  parseAvailableCommandsUpdate,
  rankCommands,
  resolveClientSlashCommand,
  sameSlashCommands,
  slashArgOptions,
} from "../src/core/composer.js";
import type { StateMessage } from "../src/core/protocol.js";

const fullState: Pick<StateMessage, "commands" | "mode" | "thinking" | "models"> = {
  commands: [
    { name: "goal", description: "Start goal-driven planning", inputHint: "<args>" },
    { name: "mcp", description: "Show MCP servers", inputHint: "<args>" },
  ],
  mode: {
    current: "ask",
    options: [
      { id: "ask", name: "Ask" },
      { id: "auto", name: "Auto" },
      { id: "always-approve", name: "Always approve" },
    ],
  },
  thinking: {
    current: "medium",
    levels: [
      { id: "high", name: "high", description: "Heavy reasoning" },
      { id: "medium", name: "medium", description: "Balanced reasoning" },
      { id: "off", name: "off", description: "No reasoning" },
    ],
  },
  models: {
    current: "gpt-4o",
    options: [
      { id: "gpt-4o", name: "GPT-4o" },
      { id: "deepseek-chat", name: "DeepSeek Chat" },
    ],
  },
};

describe("parseAvailableCommandsUpdate", () => {
  it("normalizes, deduplicates, and ignores malformed command items", () => {
    const parsed = parseAvailableCommandsUpdate([
      { name: "/goal", description: "  Run goal  ", input: { hint: " <desc> " } },
      { name: "bad name", description: "spaces not allowed" },
      { name: "", description: "empty" },
      123,
      { name: "GOAL", description: "Updated goal", input: { hint: "<new>" } },
      { name: "auto-log", description: "Show auto log" },
    ]);
    expect(parsed).toEqual([
      { name: "GOAL", description: "Updated goal", inputHint: "<new>" },
      { name: "auto-log", description: "Show auto log" },
    ]);
    expect(parseAvailableCommandsUpdate("not-an-array")).toBeUndefined();
    expect(sameSlashCommands(parsed!, [...parsed!])).toBe(true);
    expect(sameSlashCommands(parsed!, [])).toBe(false);
  });
});

describe("detectComposerTrigger", () => {
  it("detects Stage 1 slash commands only at the start of single-line input", () => {
    expect(detectComposerTrigger("/", 1)).toEqual({ kind: "slash", query: "", range: { start: 0, end: 1 } });
    expect(detectComposerTrigger("  /go", 5)).toEqual({ kind: "slash", query: "go", range: { start: 2, end: 5 } });
    expect(detectComposerTrigger("/goal foo", 3)).toEqual({ kind: "slash", query: "go", range: { start: 0, end: 5 } });
    expect(detectComposerTrigger("hello /goal", 11)).toBeUndefined();
    expect(detectComposerTrigger("line 1\n/goal", 12)).toBeUndefined();
  });

  it("detects Stage 2 slash-arg triggers for mode, thinking, and model", () => {
    expect(detectComposerTrigger("/mode ", 6)).toEqual({
      kind: "slash-arg",
      command: "mode",
      query: "",
      range: { start: 6, end: 6 },
    });
    expect(detectComposerTrigger("/thinking hi", 12)).toEqual({
      kind: "slash-arg",
      command: "thinking",
      query: "hi",
      range: { start: 10, end: 12 },
    });
    expect(detectComposerTrigger("/model deep", 11)).toEqual({
      kind: "slash-arg",
      command: "model",
      query: "deep",
      range: { start: 7, end: 11 },
    });
    expect(detectComposerTrigger("/mode ask extra", 15)).toBeUndefined();
    expect(detectComposerTrigger("/goal arg", 9)).toBeUndefined();
  });

  it("detects @ mentions anywhere (including inside /goal) while ignoring emails", () => {
    expect(detectComposerTrigger("@", 1)).toEqual({ kind: "mention", query: "", range: { start: 0, end: 1 } });
    expect(detectComposerTrigger("fix @src/a.ts please", 10)).toEqual({
      kind: "mention",
      query: "src/a",
      range: { start: 4, end: 13 },
    });
    expect(detectComposerTrigger("/goal fix @src/a", 16)).toEqual({
      kind: "mention",
      query: "src/a",
      range: { start: 10, end: 16 },
    });
    expect(detectComposerTrigger("user@example.com", 16)).toBeUndefined();
    expect(detectComposerTrigger("user_1@example.com", 18)).toBeUndefined();
    expect(detectComposerTrigger("@a,next", 2)).toEqual({ kind: "mention", query: "a", range: { start: 0, end: 2 } });
    expect(detectComposerTrigger("解释@ma然后改一下这个文件", 5)).toEqual({
      kind: "mention",
      query: "ma",
      range: { start: 2, end: 5 },
    });
    expect(detectComposerTrigger("(@ma)", 4)).toEqual({
      kind: "mention",
      query: "ma",
      range: { start: 1, end: 4 },
    });
    expect(detectComposerTrigger("see @ma.", 7)).toEqual({
      kind: "mention",
      query: "ma",
      range: { start: 4, end: 7 },
    });
    expect(detectComposerTrigger("see @a.ts:L1-L5.", 6)).toEqual({
      kind: "mention",
      query: "a",
      range: { start: 4, end: 15 },
    });
  });
});

describe("catalog, ranking, and client slash command resolution", () => {
  it("lists agent commands first and lets agent commands override client commands on collision", () => {
    const catalog = buildCommandCatalog({
      ...fullState,
      commands: [{ name: "model", description: "Agent model command" }, ...(fullState.commands ?? [])],
    });
    expect(catalog.slice(0, 3).map((c) => `${c.source}:${c.name}`)).toEqual([
      "agent:model",
      "agent:goal",
      "agent:mcp",
    ]);
    expect(catalog.filter((c) => c.name === "model")).toHaveLength(1);
  });

  it("omits conditional client commands when unavailable", () => {
    const catalog = buildCommandCatalog({
      thinking: { levels: [], disabled: "reasoning = false" },
    });
    const names = catalog.map((c) => c.name);
    expect(names).not.toContain("mode");
    expect(names).not.toContain("thinking");
    expect(names).not.toContain("model");
    expect(names).toContain("new");
    expect(names).toContain("setup");

    const askAutoOnly = buildCommandCatalog({
      mode: {
        current: "ask",
        options: [
          { id: "ask", name: "Ask" },
          { id: "auto", name: "Auto" },
        ],
      },
    });
    expect(askAutoOnly.find((c) => c.name === "mode")?.argHint).toBe("ask | auto");
    expect(buildCommandCatalog(fullState).find((c) => c.name === "mode")?.argHint).toBe("ask | auto | always-approve");
  });

  it("checks which client slash commands can run in non-ready states", () => {
    expect(canRunClientCommandInStatus({ type: "openLog" }, "busy")).toBe(true);
    expect(canRunClientCommandInStatus({ type: "openAgentConfig" }, "error")).toBe(true);
    expect(canRunClientCommandInStatus({ type: "openGuide" }, "needs-setup")).toBe(true);
    expect(canRunClientCommandInStatus({ type: "restartAgent" }, "busy")).toBe(true);
    expect(canRunClientCommandInStatus({ type: "restartAgent" }, "starting")).toBe(false);
    expect(canRunClientCommandInStatus({ type: "runSetup" }, "ready")).toBe(true);
    expect(canRunClientCommandInStatus({ type: "runSetup" }, "needs-setup")).toBe(false);
    expect(canRunClientCommandInStatus({ type: "runSetup" }, "busy")).toBe(false);
    expect(canRunClientCommandInStatus({ type: "newChat" }, "busy")).toBe(false);
    expect(canRunClientCommandInStatus({ type: "newChat" }, "ready")).toBe(true);
    expect(canRunClientCommandInStatus({ type: "setMode", value: "auto" }, "busy")).toBe(false);
    expect(canRunClientCommandInStatus({ type: "setModel", value: "gpt-4o" }, "busy")).toBe(false);
  });

  it("ranks slash commands and Stage 2 arg options", () => {
    const catalog = buildCommandCatalog(fullState);
    const ranked = rankCommands(catalog, "gl");
    expect(ranked[0]?.command.name).toBe("goal");
    expect(ranked[0]?.nameMatches).toEqual([0, 3]);

    const modes = slashArgOptions("mode", fullState, "au");
    expect(modes.map((m) => m.option.id)).toEqual(["auto"]);

    const thinking = slashArgOptions("thinking", fullState, "");
    expect(thinking.find((t) => t.option.current)?.option.id).toBe("medium");

    const models = slashArgOptions("model", fullState, "deep");
    expect(models.map((m) => m.option.id)).toEqual(["deepseek-chat"]);
  });

  it("resolves local client commands and leaves agent or unknown commands to the agent", () => {
    expect(resolveClientSlashCommand("/new", fullState)).toEqual({ kind: "message", message: { type: "newChat" } });
    expect(resolveClientSlashCommand("/restart", fullState)).toEqual({ kind: "message", message: { type: "restartAgent" } });
    expect(resolveClientSlashCommand("/setup", fullState)).toEqual({ kind: "message", message: { type: "runSetup" } });
    expect(resolveClientSlashCommand("/config", fullState)).toEqual({ kind: "message", message: { type: "openAgentConfig" } });
    expect(resolveClientSlashCommand("/log", fullState)).toEqual({ kind: "message", message: { type: "openLog" } });
    expect(resolveClientSlashCommand("/guide", fullState)).toEqual({ kind: "message", message: { type: "openGuide" } });

    expect(resolveClientSlashCommand("/mode auto", fullState)).toEqual({
      kind: "message",
      message: { type: "setMode", value: "auto" },
    });
    expect(resolveClientSlashCommand("/thinking high", fullState)).toEqual({
      kind: "message",
      message: { type: "setThinking", value: "high" },
    });
    expect(resolveClientSlashCommand("/model DeepSeek Chat", fullState)).toEqual({
      kind: "message",
      message: { type: "setModel", value: "deepseek-chat" },
    });

    expect(resolveClientSlashCommand("/new extra", fullState)).toMatchObject({ kind: "notice", level: "info" });
    expect(resolveClientSlashCommand("/mode nope", fullState)).toMatchObject({ kind: "notice", level: "warning" });
    expect(resolveClientSlashCommand("/thinking", fullState)).toMatchObject({ kind: "notice", level: "info" });

    // Agent-owned or unknown commands pass through to the agent.
    expect(resolveClientSlashCommand("/goal build feature", fullState)).toBeUndefined();
    expect(resolveClientSlashCommand("/unknown-cmd", fullState)).toBeUndefined();
  });
});
