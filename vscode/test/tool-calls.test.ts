import { describe, expect, it } from "vitest";

import { ToolCallStore, lastTextBlock, summarizeInput } from "../src/core/tool-calls.js";

describe("ToolCallStore", () => {
  it("merges the first call and its updates", () => {
    const store = new ToolCallStore();
    store.apply({ toolCallId: "a", title: "write a.txt", kind: "edit", status: "pending", rawInput: { path: "a.txt" } });
    const view = store.apply({
      toolCallId: "a",
      status: "completed",
      content: [{ type: "content", content: { type: "text", text: "ok" } }],
    });
    expect(view).toMatchObject({ id: "a", title: "write a.txt", kind: "edit", status: "completed", text: "ok", rawInput: { path: "a.txt" } });
  });

  it("joins terminal deltas and keeps the exit code", () => {
    const store = new ToolCallStore();
    store.apply({ toolCallId: "b", title: "echo", status: "pending" });
    store.apply({ toolCallId: "b", status: "in_progress", _meta: { terminal_output: { data: "one\n" } } });
    const view = store.apply({
      toolCallId: "b",
      status: "failed",
      _meta: { terminal_output: { data: "two\n" }, terminal_exit: { exit_code: 3 } },
    });
    expect(view?.terminal).toEqual({ output: "one\ntwo\n", exitCode: 3 });
    expect(view?.text).toBeUndefined();
  });

  it("reports pure terminal appends without duplicating text alongside terminal output", () => {
    const store = new ToolCallStore();
    const first = store.applyWithDelta({
      toolCallId: "b",
      title: "pytest",
      kind: "execute",
      status: "pending",
      rawInput: { command: "pytest" },
    });
    expect(first?.pureTerminalAppend).toBe(false);

    const startRunning = store.applyWithDelta({
      toolCallId: "b",
      status: "in_progress",
      content: [{ type: "content", content: { type: "text", text: "line 1\n" } }],
      _meta: { terminal_output: { data: "line 1\n" } },
    });
    expect(startRunning?.pureTerminalAppend).toBe(false);
    expect(startRunning?.view.text).toBeUndefined();

    const secondChunk = store.applyWithDelta({
      toolCallId: "b",
      status: "in_progress",
      content: [{ type: "content", content: { type: "text", text: "line 1\nline 2\n" } }],
      _meta: { terminal_output: { data: "line 2\n" } },
    });
    expect(secondChunk).toMatchObject({
      pureTerminalAppend: true,
      terminalDelta: "line 2\n",
    });
    expect(secondChunk?.view.text).toBeUndefined();
    expect(secondChunk?.view.terminal).toEqual({ output: "line 1\nline 2\n" });

    const done = store.applyWithDelta({
      toolCallId: "b",
      status: "completed",
      content: [{ type: "content", content: { type: "text", text: "line 1\nline 2\n" } }],
      _meta: { terminal_exit: { exit_code: 0 } },
    });
    expect(done?.pureTerminalAppend).toBe(false);
    expect(done?.view.terminal).toEqual({ output: "line 1\nline 2\n", exitCode: 0 });
    expect(done?.view.text).toBeUndefined();
  });

  it("ignores an update without an id and an unknown status", () => {
    const store = new ToolCallStore();
    expect(store.apply({ title: "x" })).toBeUndefined();
    expect(store.apply({ toolCallId: "c", status: "weird" })?.status).toBe("pending");
  });

  it("clears", () => {
    const store = new ToolCallStore();
    store.apply({ toolCallId: "a" });
    store.clear();
    expect(store.get("a")).toBeUndefined();
  });
});

describe("lastTextBlock and summarizeInput", () => {
  it("takes the last text block", () => {
    const content = [
      { type: "terminal", terminalId: "t" },
      { type: "content", content: { type: "text", text: "first" } },
      { type: "content", content: { type: "text", text: "last" } },
    ];
    expect(lastTextBlock(content)).toBe("last");
    expect(lastTextBlock(undefined)).toBeUndefined();
  });

  it("shows the whole command, or the path", () => {
    expect(summarizeInput({ command: "ls -la", path: "x" })).toBe("ls -la");
    expect(summarizeInput({ path: "a.txt" })).toBe("a.txt");
    expect(summarizeInput(null)).toBe("");
  });

  it("shows the whole script of a codemode call (code) and of a workflow call (script)", () => {
    const code = Array.from({ length: 200 }, (_, i) => `result_${i} = await tools.read(path="f${i}.txt")`).join("\n");
    expect(summarizeInput({ code, timeout_ms: 5000 })).toBe(code);
    const script = `async def main():\n    await agent("review ${"x".repeat(2000)}")\n`;
    expect(summarizeInput({ script, max_agents: 3, args: { a: 1 } })).toBe(script);
  });

  it("shows the built-in workflow that runs, and not the script that the agent ignores, for a workflow call", () => {
    const script = "async def main():\n    await agent('x')\n";
    const ignored = "The agent ignores `script` when `name` is set.";
    for (const title of ["workflow", "workflow (codemode)", "workflow (Auto: no rule; workflow sub-agent in /p)"]) {
      expect(summarizeInput({ name: "deep-research", script }, title)).toBe(`built-in workflow: deep-research\n${ignored}`);
      expect(summarizeInput({ name: "deep-research", script }, title)).not.toContain("async def");
      expect(summarizeInput({ name: "deep-research", args: { question: "q" } }, title)).toBe(
        'built-in workflow: deep-research\nargs: {"question":"q"}',
      );
      expect(summarizeInput({ name: "deep-research" }, title)).toBe("built-in workflow: deep-research");
      expect(summarizeInput({ name: "deep-research", args: { question: "q" }, script }, title)).toBe(
        `built-in workflow: deep-research\nargs: {"question":"q"}\n${ignored}`,
      );
    }
    const question = "why ".repeat(5000);
    expect(summarizeInput({ name: "w", args: { question, list: [1, { a: null }] } }, "workflow")).toBe(
      `built-in workflow: w\nargs: ${JSON.stringify({ question, list: [1, { a: null }] })}`,
    );
    expect(summarizeInput({ name: "w", args: null }, "workflow")).toBe("built-in workflow: w");
    expect(summarizeInput({ script }, "workflow")).toBe(script);
    expect(summarizeInput({ name: "", script }, "workflow")).toBe(script);
    expect(summarizeInput({ name: 5, script }, "workflow")).toBe(script);
    expect(summarizeInput({ name: "", args: { a: 1 } }, "workflow")).toBe("");
  });

  it("gives `name` no meaning for a tool that is not workflow, and for a call with no title", () => {
    const script = "async def main():\n    pass\n";
    expect(summarizeInput({ name: "deep-research" })).toBe("");
    expect(summarizeInput({ name: "review", args: {} })).toBe("");
    expect(summarizeInput({ name: "deep-research" }, undefined)).toBe("");
    expect(summarizeInput({ name: "deep-research" }, "")).toBe("");
    for (const title of ["mcp__github__create_repository", "mcp__demo__create (codemode)", "create_repository", "workflows", "my-workflow", "workflow_x"]) {
      expect(summarizeInput({ name: "deep-research" }, title)).toBe("");
      expect(summarizeInput({ name: "my-repo", private: true }, title)).toBe("");
      expect(summarizeInput({ name: "my-repo", args: { a: 1 } }, title)).toBe("");
    }
    expect(summarizeInput({ name: "my-repo", script }, "mcp__github__create_repository")).toBe(script);
    expect(summarizeInput({ name: "my-repo", script })).toBe(script);
    expect(summarizeInput({ name: "my-repo", code: "c" }, "mcp__demo__run")).toBe("c");
    expect(summarizeInput({ name: "my-repo", path: "p" }, "workflow")).toBe("p");
    expect(summarizeInput({ name: "my-repo", command: "ls" }, "workflow")).toBe("ls");
  });

  it("reads the command first, then the path, then the code, then the script, and skips values that are not text", () => {
    expect(summarizeInput({ code: "c", script: "s" })).toBe("c");
    expect(summarizeInput({ name: "n", code: "c" })).toBe("c");
    expect(summarizeInput({ path: "p", code: "c" })).toBe("p");
    expect(summarizeInput({ command: 1, path: "p" })).toBe("p");
    expect(summarizeInput({ code: null, script: "s" })).toBe("s");
    expect(summarizeInput({ args: {}, timeout_ms: 5 })).toBe("");
    expect(summarizeInput({ query: "x" })).toBe("");
    expect(summarizeInput([{ code: "c" }])).toBe("");
  });
});
