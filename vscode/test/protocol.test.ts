import { describe, expect, it } from "vitest";

import { parseMcpServers } from "../src/core/host.js";
import { isSafeLink, parseWebviewMessage } from "../src/core/protocol.js";

describe("parseWebviewMessage", () => {
  it("accepts the simple messages", () => {
    for (const type of ["ready", "cancel", "newChat", "runSetup", "restartAgent", "openLog", "openGuide", "openAgentConfig", "saveAll"]) {
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

  it("accepts send with valid images even when text is empty", () => {
    expect(
      parseWebviewMessage({
        type: "send",
        text: "",
        images: [{ mimeType: "image/png", data: "iVBORw0KGgo=", name: "screen.png" }],
      }),
    ).toEqual({
      type: "send",
      text: "",
      images: [{ mimeType: "image/png", data: "iVBORw0KGgo=", name: "screen.png" }],
    });
    expect(parseWebviewMessage({ type: "send", text: "hi", images: [] })).toEqual({ type: "send", text: "hi" });
  });

  it("accepts openFile with normalized path and optional line", () => {
    expect(parseWebviewMessage({ type: "openFile", path: "./src\\app.ts" })).toEqual({
      type: "openFile",
      path: "src/app.ts",
    });
    expect(parseWebviewMessage({ type: "openFile", path: "src/app.ts", line: 42 })).toEqual({
      type: "openFile",
      path: "src/app.ts",
      line: 42,
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
      { type: "send", text: "   ", images: [] },
      { type: "send", text: "x".repeat(200_001) },
      { type: "send", text: "hi", mentions: "bad" },
      { type: "send", text: "hi", mentions: [{ kind: "file", path: "../secret.txt" }] },
      { type: "send", text: "hi", mentions: [{ kind: "file", path: "/etc/passwd" }] },
      { type: "send", text: "hi", mentions: [{ kind: "file", path: "a.ts", range: { startLine: 1, endLine: 2 } }] },
      { type: "send", text: "hi", mentions: [{ kind: "selection", path: "a.ts" }] },
      { type: "send", text: "hi", mentions: [{ kind: "selection", path: "a.ts", range: { startLine: 5, endLine: 2 } }] },
      { type: "send", text: "hi", mentions: [{ kind: "selection", path: "a.ts", range: { startLine: 0, endLine: 2 } }] },
      { type: "send", text: "hi", mentions: Array.from({ length: 21 }, (_, i) => ({ kind: "file", path: `f${i}.ts` })) },
      { type: "send", text: "hi", images: [{ mimeType: "image/svg+xml", data: "AAAA" }] },
      { type: "send", text: "hi", images: [{ mimeType: "image/png", data: "not base64!" }] },
      { type: "send", text: "hi", images: [{ mimeType: "image/png", data: "" }] },
      { type: "send", text: "hi", images: Array.from({ length: 5 }, () => ({ mimeType: "image/png", data: "AAAA" })) },
      { type: "permissionAnswer", requestId: "r" },
      { type: "permissionAnswer", requestId: "", optionId: "o" },
      { type: "showDiff", toolCallId: 5 },
      { type: "setMode", value: "yolo" },
      { type: "setModel", value: "" },
      { type: "queryMentions", requestId: -1, query: "" },
      { type: "queryMentions", requestId: 1.5, query: "" },
      { type: "queryMentions", requestId: 1, query: "q".repeat(257) },
      { type: "openFile", path: "../secret.txt" },
      { type: "openFile", path: "/etc/passwd" },
      { type: "openFile", path: "src/a.ts", line: 0 },
      { type: "openFile", path: "src/a.ts", line: -3 },
      { type: "openLink", url: "" },
    ];
    for (const value of bad) expect(parseWebviewMessage(value)).toBeUndefined();
  });

  it("accepts the two modes", () => {
    expect(parseWebviewMessage({ type: "setMode", value: "auto" })).toEqual({ type: "setMode", value: "auto" });
    expect(parseWebviewMessage({ type: "setMode", value: "ask" })).toEqual({ type: "setMode", value: "ask" });
  });
});

describe("parseMcpServers", () => {
  it("normalizes stdio, http, and sse entries and skips invalid configs", () => {
    expect(parseMcpServers(null)).toEqual([]);
    expect(
      parseMcpServers([
        { name: " local-fs ", command: " npx ", args: ["-y", "mcp-fs"], env: [{ name: "ROOT", value: "/tmp" }] },
        { type: "stdio", name: "git", command: "uvx" },
        { type: "http", name: "remote-http", url: "https://mcp.example.com/http", headers: [{ name: "Authorization", value: "Bearer x" }] },
        { type: "sse", name: "remote-sse", url: "https://mcp.example.com/sse" },
        { name: "", command: "bad" },
        { name: "no-cmd" },
        { type: "http", name: "no-url" },
        { name: "bad-args", command: "node", args: [123] },
        { name: "bad-env", command: "node", env: [{ name: "", value: "1" }] },
      ]),
    ).toEqual([
      { name: "local-fs", command: "npx", args: ["-y", "mcp-fs"], env: [{ name: "ROOT", value: "/tmp" }] },
      { name: "git", command: "uvx", args: [], env: [] },
      { type: "http", name: "remote-http", url: "https://mcp.example.com/http", headers: [{ name: "Authorization", value: "Bearer x" }] },
      { type: "sse", name: "remote-sse", url: "https://mcp.example.com/sse", headers: [] },
    ]);
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
