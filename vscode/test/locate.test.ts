import { chmodSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { bundledBinaryName, prepareBundledBinary, resolveAgentCommand } from "../src/core/locate.js";
import { tempDir } from "./helpers.js";

const base = { agentPath: "", agentArgs: [], extensionPath: "/ext", platform: "linux" as const, exists: () => false };

describe("resolveAgentCommand", () => {
  it("uses the setting first, with its arguments", () => {
    const result = resolveAgentCommand({ ...base, agentPath: " /usr/bin/python3 ", agentArgs: ["-m", "pi_agent_cli"] });
    expect(result).toEqual({
      ok: true,
      command: { command: "/usr/bin/python3", args: ["-m", "pi_agent_cli"], source: "setting" },
    });
  });

  it("accepts a bare program name and does not search PATH itself", () => {
    const result = resolveAgentCommand({ ...base, agentPath: "python" });
    expect(result).toMatchObject({ ok: true, command: { command: "python", source: "setting" } });
  });

  it("refuses a relative path that has a folder part", () => {
    for (const agentPath of ["./agent", "bin/agent", "..\\agent"]) {
      const result = resolveAgentCommand({ ...base, agentPath });
      expect(result.ok).toBe(false);
    }
  });

  it("accepts an absolute Windows path on win32", () => {
    const result = resolveAgentCommand({ ...base, agentPath: "C:\\tools\\agent.exe", platform: "win32" });
    expect(result.ok).toBe(true);
  });

  it("falls back to the bundled binary when it exists", () => {
    const result = resolveAgentCommand({ ...base, exists: (file) => file === "/ext/bin/zycode-agent" });
    expect(result).toEqual({ ok: true, command: { command: "/ext/bin/zycode-agent", args: [], source: "bundled" } });
  });

  it("uses the .exe name on Windows", () => {
    expect(bundledBinaryName("win32")).toBe("zycode-agent.exe");
    const result = resolveAgentCommand({
      ...base,
      platform: "win32",
      extensionPath: "C:\\ext",
      exists: (file) => file === "C:\\ext\\bin\\zycode-agent.exe",
    });
    expect(result.ok).toBe(true);
  });

  it("says what to do when nothing is available", () => {
    const result = resolveAgentCommand(base);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("zycode.agentPath");
  });
});

describe("prepareBundledBinary", () => {
  const dirs: string[] = [];
  afterEach(() => {
    dirs.length = 0;
  });

  it.skipIf(process.platform === "win32")("sets the exec bit", async () => {
    const dir = tempDir(dirs, "bin-");
    const file = path.join(dir, "zycode-agent");
    writeFileSync(file, "#!/bin/sh\n");
    chmodSync(file, 0o644);
    expect(await prepareBundledBinary(file, process.platform)).toBeUndefined();
    expect(statSync(file).mode & 0o111).toBe(0o111);
  });

  it("returns a problem text, and does not throw, for a missing file", async () => {
    if (process.platform === "win32") return;
    const dir = tempDir(dirs, "bin-");
    mkdirSync(dir, { recursive: true });
    const problem = await prepareBundledBinary(path.join(dir, "missing"), process.platform);
    expect(problem).toContain("Could not set the exec bit");
  });

  it("does nothing on Windows", async () => {
    expect(await prepareBundledBinary("C:\\nowhere\\agent.exe", "win32")).toBeUndefined();
  });
});
