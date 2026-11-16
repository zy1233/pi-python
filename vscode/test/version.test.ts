import { describe, expect, it } from "vitest";

import { agentVersionAtLeast, checkAgentIdentity } from "../src/core/version.js";

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

describe("agentVersionAtLeast", () => {
  const atLeast = (version: string | undefined) => agentVersionAtLeast(version, "1.0.0");

  it("compares the numbers as numbers, not as text", () => {
    expect(atLeast("0.9.1")).toBe(false);
    expect(atLeast("0.10.0")).toBe(false);
    expect(atLeast("1.0.0")).toBe(true);
    expect(atLeast("1.0.1")).toBe(true);
    expect(atLeast("1.2.0")).toBe(true);
    expect(atLeast("2.0.0")).toBe(true);
    expect(atLeast("10.0.0")).toBe(true);
    expect(agentVersionAtLeast("1.10.0", "1.9.0")).toBe(true);
    expect(agentVersionAtLeast("1.9.0", "1.10.0")).toBe(false);
  });

  it("reads a version with fewer than three numbers, and one with more", () => {
    expect(atLeast("1.0")).toBe(true);
    expect(atLeast("1")).toBe(true);
    expect(atLeast("0.9")).toBe(false);
    expect(atLeast("1.0.0.1")).toBe(true);
  });

  it("says no to a pre-release of the version itself, which can come from before the change", () => {
    for (const version of ["1.0.0rc1", "1.0.0a1", "1.0.0b2", "1.0.0.dev0", "1.0.0.dev3", "1.0.dev0"]) {
      expect(atLeast(version), version).toBe(false);
    }
  });

  it("says yes to a pre-release of a later version, to a post-release and to a local version", () => {
    expect(atLeast("1.0.1rc1")).toBe(true);
    expect(atLeast("1.1.0.dev2")).toBe(true);
    expect(atLeast("1.0.0.post1")).toBe(true);
    expect(atLeast("1.0.0+local")).toBe(true);
    expect(atLeast("1.0.0+abc.5")).toBe(true);
  });

  it("says no when it cannot read the version", () => {
    for (const version of [undefined, "", "unknown", "v1.0.0", "0.0.0+unknown", "a.b.c", "1.0.0!"]) {
      expect(atLeast(version), String(version)).toBe(false);
    }
  });

  it("says no when the version after the numbers is not a PEP 440 suffix", () => {
    expect(atLeast("1.0.0-beta")).toBe(false);
    expect(atLeast("1.0.0abc")).toBe(false);
    expect(atLeast("1!1.0.0")).toBe(false);
  });
});