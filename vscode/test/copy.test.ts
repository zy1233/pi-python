import { describe, expect, it } from "vitest";

import { cleanCodeLine, codeLanguageLabel, isShellLang, toolCopyText } from "../src/core/copy.js";

describe("isShellLang and codeLanguageLabel", () => {
  it("recognizes shell language identifiers regardless of case or surrounding whitespace", () => {
    for (const lang of ["sh", "bash", "ZSH", " PowerShell ", "ps1", "pwsh", "cmd", "bat", "console", "fish", "shell"]) {
      expect(isShellLang(lang), lang).toBe(true);
    }
    for (const lang of ["", "ts", "typescript", "python", "json"]) {
      expect(isShellLang(lang), lang).toBe(false);
    }
  });

  it("normalizes language labels and falls back to code", () => {
    expect(codeLanguageLabel("TS")).toBe("ts");
    expect(codeLanguageLabel("  Python  ")).toBe("python");
    expect(codeLanguageLabel("")).toBe("code");
    expect(codeLanguageLabel("   ")).toBe("code");
  });
});

describe("cleanCodeLine", () => {
  it("trims trailing horizontal whitespace and CR while preserving leading indentation", () => {
    expect(cleanCodeLine("  const x = 1;  \t\r", "ts")).toBe("  const x = 1;");
    expect(cleanCodeLine("\treturn true;\r", "python")).toBe("\treturn true;");
  });

  it("strips leading $ and PS> prompts for shell or untagged snippets", () => {
    expect(cleanCodeLine("$ npm test", "bash")).toBe("npm test");
    expect(cleanCodeLine("  $   git status  \r", "sh")).toBe("git status");
    expect(cleanCodeLine("$ echo hi", "")).toBe("echo hi");
    expect(cleanCodeLine("PS> Get-ChildItem -Force", "powershell")).toBe("Get-ChildItem -Force");
    expect(cleanCodeLine("  PS> npm run check  ", "")).toBe("npm run check");
  });

  it("does not strip shell variables, bare prompts, or non-shell languages", () => {
    expect(cleanCodeLine("$HOME/bin/run", "bash")).toBe("$HOME/bin/run");
    expect(cleanCodeLine("  $   ", "bash")).toBe("  $");
    expect(cleanCodeLine("$ price = 10", "php")).toBe("$ price = 10");
    expect(cleanCodeLine("$('#app').hide()", "js")).toBe("$('#app').hide()");
  });
});

describe("toolCopyText", () => {
  it("prefers rawInput.command, then rawInput.path, then title", () => {
    expect(toolCopyText({ title: "run tests", rawInput: { command: "npm test", path: "package.json" } })).toEqual({
      text: "npm test",
      label: "Copy command",
    });
    expect(toolCopyText({ title: "read file", rawInput: { path: "src/app.ts" } })).toEqual({
      text: "src/app.ts",
      label: "Copy path",
    });
    expect(toolCopyText({ title: "list files", rawInput: { command: "   ", path: "" } })).toEqual({
      text: "list files",
      label: "Copy",
    });
    expect(toolCopyText({ title: "bash", rawInput: ["not", "an", "object"] })).toEqual({
      text: "bash",
      label: "Copy",
    });
    expect(toolCopyText({ title: "search" })).toEqual({
      text: "search",
      label: "Copy",
    });
  });
});
