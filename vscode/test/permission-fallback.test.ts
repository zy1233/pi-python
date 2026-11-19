import { describe, expect, it } from "vitest";

import { TRUST_NOTE } from "../src/core/chat-controller.js";
import { buildPermissionFallback, resolveFallbackChoice } from "../src/core/permission-fallback.js";
import type { PermissionRequestView } from "../src/core/protocol.js";

describe("permission fallback dialog model", () => {
  it("keeps the full bash command, exact option order, and appends Open chat", () => {
    const longCommand = `echo ${"x".repeat(500)} && ls -la`;
    const request: PermissionRequestView = {
      requestId: "perm-1",
      kind: "tool",
      toolCallId: "call-1",
      title: "bash",
      summary: longCommand,
      canDiff: false,
      options: [
        { id: "allow-once", name: "Allow once", kind: "allow_once" },
        { id: "reject-once", name: "Reject", kind: "reject_once" },
      ],
    };

    const dialog = buildPermissionFallback(request);
    expect(dialog.message).toBe("zycode — Allow: bash");
    expect(dialog.detail).toContain(longCommand);
    expect(dialog.detail).toContain("keeps the request waiting");
    expect(dialog.items).toEqual([
      { title: "Allow once", kind: "answer", optionId: "allow-once" },
      { title: "Reject", kind: "answer", optionId: "reject-once" },
      { title: "Open chat", kind: "reveal" },
    ]);
    // Never invent an allow-always option the agent did not send.
    expect(dialog.items.some((item) => item.title.toLowerCase().includes("always"))).toBe(false);
  });

  it("mentions diff availability or diffNote when present", () => {
    const withDiff = buildPermissionFallback({
      requestId: "perm-2",
      kind: "tool",
      toolCallId: "call-2",
      title: "edit src/app.ts",
      summary: "src/app.ts",
      canDiff: true,
      options: [{ id: "allow-once", name: "Allow once", kind: "allow_once" }],
    });
    expect(withDiff.detail).toContain("Open chat to inspect the diff before deciding.");

    const withoutDiff = buildPermissionFallback({
      requestId: "perm-3",
      kind: "tool",
      toolCallId: "call-3",
      title: "edit src/app.ts",
      summary: "src/app.ts",
      canDiff: false,
      diffNote: "diff not available",
      options: [{ id: "allow-once", name: "Allow once", kind: "allow_once" }],
    });
    expect(withoutDiff.detail).toContain("diff not available");
  });

  it("formats project-trust prompts with TRUST_NOTE and exact options", () => {
    const dialog = buildPermissionFallback({
      requestId: "perm-trust",
      kind: "trust",
      toolCallId: "trust-1",
      title: "loading this project's extensions",
      summary: TRUST_NOTE,
      canDiff: false,
      options: [
        { id: "dont-trust-project", name: "Don't trust", kind: "reject_once" },
        { id: "trust-project", name: "Trust and remember", kind: "allow_always" },
      ],
    });
    expect(dialog.message).toBe("zycode — Project trust: loading this project's extensions");
    expect(dialog.detail).toContain("not the VS Code Workspace Trust");
    expect(dialog.items.map((item) => item.title)).toEqual(["Don't trust", "Trust and remember", "Open chat"]);
  });

  it("maps modal choices safely and never auto-answers when dismissed", () => {
    expect(resolveFallbackChoice(undefined)).toEqual({ kind: "dismiss" });
    expect(resolveFallbackChoice({ title: "Open chat", kind: "reveal" })).toEqual({ kind: "reveal" });
    expect(resolveFallbackChoice({ title: "Allow once", kind: "answer", optionId: "allow-once" })).toEqual({
      kind: "answer",
      optionId: "allow-once",
    });
  });
});
