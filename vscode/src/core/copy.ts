/**
 * Pure helpers for copying commands, file paths, and code lines in the chat view.
 * No `vscode` or DOM imports (docs/specs/2026-11-04-vscode-extension-design.md, section 2).
 */

export const SHELL_LANGS = new Set([
  "sh",
  "bash",
  "zsh",
  "shell",
  "console",
  "fish",
  "powershell",
  "ps1",
  "pwsh",
  "cmd",
  "bat",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Whether `lang` identifies a shell or terminal snippet. */
export function isShellLang(lang: string): boolean {
  return SHELL_LANGS.has(lang.trim().toLowerCase());
}

/** Normalizes a fenced code block language tag for display, falling back to `"code"`. */
export function codeLanguageLabel(lang: string): string {
  return lang.trim().toLowerCase() || "code";
}

/**
 * Prepares a single line from a code block for clipboard copy.
 * Trims trailing horizontal whitespace and strips leading shell prompt prefixes (`$ `, `PS> `)
 * for shell or untagged snippets so commands can be pasted directly into a terminal.
 */
export function cleanCodeLine(line: string, lang = ""): string {
  const trimmed = line.replace(/[ \t\r]+$/, "");
  if (lang.trim() === "" || isShellLang(lang)) {
    const promptMatch = /^\s*(?:\$|PS>)\s+(\S.*)$/.exec(trimmed);
    if (promptMatch?.[1] !== undefined) return promptMatch[1];
  }
  return trimmed;
}

/** Chooses the most useful copy payload and accessible label for a tool call card. */
export function toolCopyText(call: { title: string; rawInput?: unknown }): { text: string; label: string } {
  if (isRecord(call.rawInput)) {
    if (typeof call.rawInput.command === "string" && call.rawInput.command.trim() !== "") {
      return { text: call.rawInput.command, label: "Copy command" };
    }
    if (typeof call.rawInput.path === "string" && call.rawInput.path.trim() !== "") {
      return { text: call.rawInput.path, label: "Copy path" };
    }
  }
  return { text: call.title, label: "Copy" };
}
