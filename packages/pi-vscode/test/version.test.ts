import { describe, expect, it } from "vitest";

import { checkAgentIdentity } from "../src/core/version.js";

const ok = { protocolVersion: 1, agentName: "pi-agent-cli", agentVersion: "0.8.0", source: "bundled" as const, expectedVersion: "0.8.0" };

describe("checkAgentIdentity", () => {
  it("passes a bundled agent of the pinned version", () => {
    expect(checkAgentIdentity(ok)).toEqual({ kind: "ok" });
  });

  it("warns for a bundled agent of another version", () => {
    const verdict = checkAgentIdentity({ ...ok, agentVersion: "0.9.0" });
    expect(verdict.kind).toBe("warn");
  });

  it("only logs the version of an agent that the user chose", () => {
    const verdict = checkAgentIdentity({ ...ok, source: "setting", agentVersion: "9.9.9" });
    expect(verdict).toMatchObject({ kind: "ok" });
    expect(verdict.kind === "ok" && verdict.log).toContain("9.9.9");
  });

  it("fails a program with another protocol version or another name", () => {
    expect(checkAgentIdentity({ ...ok, protocolVersion: 2 }).kind).toBe("fail");
    expect(checkAgentIdentity({ ...ok, agentName: "other" }).kind).toBe("fail");
    expect(checkAgentIdentity({ ...ok, agentName: undefined, source: "setting" }).kind).toBe("fail");
  });
});
