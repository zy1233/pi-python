import { describe, expect, it } from "vitest";

import { isSafeLink, parseWebviewMessage } from "../src/core/protocol.js";

describe("parseWebviewMessage", () => {
  it("accepts the simple messages", () => {
    for (const type of ["ready", "cancel", "newChat", "runSetup", "restartAgent", "openLog", "openGuide", "openAgentConfig"]) {
      expect(parseWebviewMessage({ type })).toEqual({ type });
    }
  });

  it("copies only the known fields", () => {
    expect(parseWebviewMessage({ type: "send", text: "hi", extra: 1 })).toEqual({ type: "send", text: "hi" });
    expect(parseWebviewMessage({ type: "permissionAnswer", requestId: "r", optionId: "o", x: 1 })).toEqual({
      type: "permissionAnswer",
      requestId: "r",
      optionId: "o",
    });
  });

  it("accepts the three modes and no other", () => {
    for (const value of ["ask", "auto", "always-approve"]) {
      expect(parseWebviewMessage({ type: "setMode", value })).toEqual({ type: "setMode", value });
    }
    for (const value of ["yolo", "", 1, undefined]) expect(parseWebviewMessage({ type: "setMode", value })).toBeUndefined();
  });

  it("accepts send with valid mentions and omits empty mentions", () => {
    expect(parseWebviewMessage({ type: "send", text: "hi", mentions: [] })).toEqual({ type: "send", text: "hi" });
    expect(
      parseWebviewMessage({
        type: "send",
        text: "check",
        mentions: [
          { kind: "file", path: "./src\\app.ts", extra: 1 },
          { kind: "folder", path: "src/core" },
          { kind: "selection", path: "src/app.ts", range: { startLine: 3, endLine: 7 } },
        ],
      }),
    ).toEqual({
      type: "send",
      text: "check",
      mentions: [
        { kind: "file", path: "src/app.ts" },
        { kind: "folder", path: "src/core/" },
        { kind: "selection", path: "src/app.ts", range: { startLine: 3, endLine: 7 } },
      ],
    });
  });

  it("accepts setModel and queryMentions", () => {
    expect(parseWebviewMessage({ type: "setModel", value: "gpt-4o", extra: true })).toEqual({
      type: "setModel",
      value: "gpt-4o",
    });
    expect(parseWebviewMessage({ type: "queryMentions", requestId: 0, query: "src/", extra: true })).toEqual({
      type: "queryMentions",
      requestId: 0,
      query: "src/",
    });
  });

  it("refuses bad input", () => {
    const bad: unknown[] = [
      null,
      "send",
      [],
      {},
      { type: 1 },
      { type: "unknown" },
      { type: "send" },
      { type: "send", text: "   " },
      { type: "send", text: "x".repeat(200_001) },
      { type: "send", text: "hi", mentions: "bad" },
      { type: "send", text: "hi", mentions: [{ kind: "file", path: "../secret.txt" }] },
      { type: "send", text: "hi", mentions: [{ kind: "file", path: "/etc/passwd" }] },
      { type: "send", text: "hi", mentions: [{ kind: "file", path: "a.ts", range: { startLine: 1, endLine: 2 } }] },
      { type: "send", text: "hi", mentions: [{ kind: "selection", path: "a.ts" }] },
      { type: "send", text: "hi", mentions: [{ kind: "selection", path: "a.ts", range: { startLine: 5, endLine: 2 } }] },
      { type: "send", text: "hi", mentions: [{ kind: "selection", path: "a.ts", range: { startLine: 0, endLine: 2 } }] },
      { type: "send", text: "hi", mentions: Array.from({ length: 21 }, (_, i) => ({ kind: "file", path: `f${i}.ts` })) },
      { type: "permissionAnswer", requestId: "r" },
      { type: "permissionAnswer", requestId: "", optionId: "o" },
      { type: "showDiff", toolCallId: 5 },
      { type: "setMode", value: "yolo" },
      { type: "setModel", value: "" },
      { type: "queryMentions", requestId: -1, query: "" },
      { type: "queryMentions", requestId: 1.5, query: "" },
      { type: "queryMentions", requestId: 1, query: "q".repeat(257) },
      { type: "openLink", url: "" },
    ];
    for (const value of bad) expect(parseWebviewMessage(value)).toBeUndefined();
  });

  it("accepts the two modes", () => {
    expect(parseWebviewMessage({ type: "setMode", value: "auto" })).toEqual({ type: "setMode", value: "auto" });
    expect(parseWebviewMessage({ type: "setMode", value: "ask" })).toEqual({ type: "setMode", value: "ask" });
  });
});

describe("isSafeLink", () => {
  it("allows http, https and mailto", () => {
    expect(isSafeLink("https://zycode.dev/x")).toBe(true);
    expect(isSafeLink("http://localhost:3000")).toBe(true);
    expect(isSafeLink("mailto:a@b.c")).toBe(true);
  });

  it("refuses the rest", () => {
    for (const url of ["file:///etc/passwd", "javascript:alert(1)", "command:workbench.action.reloadWindow", "vscode://x", "/relative", "data:text/html,x", ""]) {
      expect(isSafeLink(url)).toBe(false);
    }
  });
});
