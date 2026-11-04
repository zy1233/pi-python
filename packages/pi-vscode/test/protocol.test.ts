import { describe, expect, it } from "vitest";

import { isSafeLink, parseWebviewMessage } from "../src/core/protocol.js";

describe("parseWebviewMessage", () => {
  it("accepts the simple messages", () => {
    for (const type of ["ready", "cancel", "newChat", "runSetup", "restartAgent", "openLog"]) {
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
      { type: "permissionAnswer", requestId: "r" },
      { type: "permissionAnswer", requestId: "", optionId: "o" },
      { type: "showDiff", toolCallId: 5 },
      { type: "setMode", value: "yolo" },
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
