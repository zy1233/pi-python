/**
 * The thinking level (docs/specs/2026-11-04-vscode-extension-design.md, section 7c). It adds no wire
 * item and no agent change.
 *
 * What agent v0.9.0 offers (read from its source and measured on the 0.9.0 binary):
 * - The level lives in `agent.toml` only: the top-level key `thinking_level`, one of `off`,
 *   `minimal`, `low`, `medium`, `high`, `xhigh`. A missing or invalid value is `off`.
 * - The agent reads it when the process starts. `session/new` has no thinking option or `_meta`,
 *   `session/set_config_option` refuses any id but `model` and `mode`, and the agent has no
 *   custom method for it. The harness has `set_thinking_level`, but the ACP agent never calls it.
 * - `Model.reasoning` is `reasoning` in `[model]` (or at the top level) when it is set, else
 *   `thinking_level != "off"` (`CliConfig.model_reasoning`). With `reasoning = false` the agent
 *   asks no provider to think, whatever the level is.
 * - `zypi` has no level switch for this agent either: it refuses `--reasoning-effort` and says
 *   "Set thinking_level in agent.toml."
 *
 * So the only input the agent has is the file. This module reads it, and changes the one key with a
 * minimal text edit that is checked by parsing the result.
 */

import { existsSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

import { parse as parseToml } from "smol-toml";

import type { ThinkingLevel, ThinkingLevelOption } from "./protocol.js";

/** The order of the pager menu (`effort_levels.rs`): strongest first. The ids are the agent's. */
export const THINKING_LEVELS: readonly ThinkingLevelOption[] = [
  { id: "xhigh", name: "xhigh", description: "Extended reasoning" },
  { id: "high", name: "high", description: "Heavy reasoning" },
  { id: "medium", name: "medium", description: "Balanced reasoning" },
  { id: "low", name: "low", description: "Faster, lighter reasoning" },
  { id: "minimal", name: "minimal", description: "Minimal reasoning" },
  { id: "off", name: "off", description: "No reasoning" },
];

export function isThinkingLevel(value: unknown): value is ThinkingLevel {
  return typeof value === "string" && THINKING_LEVELS.some((level) => level.id === value);
}

/** What `agent.toml` says about thinking. */
export interface ThinkingFile {
  /** The level the agent uses: `off` when the key is missing or invalid. */
  level: ThinkingLevel;
  /** `reasoning` of `[model]` or of the top level; `undefined` when it is not set. */
  reasoning: boolean | undefined;
  /** The value of `thinking_level` when the agent cannot use it. */
  invalidLevel?: string;
}

export type ThinkingRead = { ok: true; value: ThinkingFile } | { ok: false; reason: string };

/** `_as_bool` of the agent (`config.py`). */
function asBool(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
  if (typeof value === "number" || typeof value === "bigint") return value !== 0 && value !== 0n;
  if (Array.isArray(value)) return value.length > 0;
  if (value instanceof Date) return true;
  if (isTable(value)) return Object.keys(value).length > 0;
  return false;
}

function isTable(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !(value instanceof Date);
}

function stripBom(text: string): string {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

export function readThinking(text: string): ThinkingRead {
  let data: Record<string, unknown>;
  try {
    data = parseToml(stripBom(text));
  } catch {
    return { ok: false, reason: "agent.toml is not valid TOML, so zycode cannot read the thinking level. Fix the file and restart the agent." };
  }
  const raw = data.thinking_level;
  const value: ThinkingFile = { level: "off", reasoning: undefined };
  if (raw !== undefined) {
    if (isThinkingLevel(raw)) value.level = raw;
    else value.invalidLevel = typeof raw === "string" ? raw : typeof raw;
  }
  const model = isTable(data.model) ? data.model : {};
  const reasoning = model.reasoning ?? data.reasoning;
  if (reasoning !== undefined && reasoning !== null) value.reasoning = asBool(reasoning);
  return { ok: true, value };
}

/** Why the picker is off: the model cannot think, or `undefined` when it can. */
export function noThinkingReason(value: ThinkingFile): string | undefined {
  if (value.reasoning === false) {
    return "agent.toml sets reasoning = false for the model, so the agent asks no provider to think and a level has no effect. Remove that line to use a level.";
  }
  return undefined;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime() && a.constructor === b.constructor;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, i) => sameValue(item, b[i]));
  if (isTable(a) && isTable(b)) {
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every((key) => Object.hasOwn(b, key) && sameValue(a[key], b[key]));
  }
  return false;
}

const LEVEL_LINE = /^(\s*)thinking_level(\s*=\s*)(?:"[^"\n]*"|'[^'\n]*')(\s*(?:#.*)?)$/;
const TABLE_HEADER = /^\s*\[/;
const COMMENT = /^\s*#/;

/**
 * The line of the first table header (`[x]` or `[[x]]`), or -1. A line that starts with `[` inside a
 * multi-line string or inside a multi-line array is not a header. This is a scan, not a parser: a
 * wrong answer is caught by the check after the edit.
 */
function firstTableHeader(lines: string[]): number {
  let multi: string | undefined;
  let depth = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string;
    if (multi === undefined && depth === 0 && TABLE_HEADER.test(line)) return i;
    let at = 0;
    if (multi !== undefined) {
      const end = line.indexOf(multi);
      if (end < 0) continue;
      multi = undefined;
      at = end + 3;
    }
    let quote: string | undefined;
    for (; at < line.length; at++) {
      const ch = line.charAt(at);
      if (quote !== undefined) {
        if (ch === "\\" && quote === '"') at++;
        else if (ch === quote) quote = undefined;
      } else if (ch === "#") {
        break;
      } else if (ch === '"' || ch === "'") {
        if (line.startsWith(ch.repeat(3), at)) {
          const end = line.indexOf(ch.repeat(3), at + 3);
          if (end < 0) {
            multi = ch.repeat(3);
            break;
          }
          at = end + 2;
        } else {
          quote = ch;
        }
      } else if (ch === "[") {
        depth++;
      } else if (ch === "]" && depth > 0) {
        depth--;
      }
    }
  }
  return -1;
}

export type EditResult = { ok: true; text: string } | { ok: false; reason: string };

/**
 * `text` with `thinking_level` set to `level`, and nothing else changed (comments, order and line
 * ends stay). The edit is a guess about the layout of the file, so it is checked: both texts are
 * parsed, and the new one must be the old one with only `thinking_level` changed. Otherwise
 * nothing is returned, and the file is not touched.
 */
export function setThinkingLevelInText(text: string, level: ThinkingLevel): EditResult {
  let before: Record<string, unknown>;
  try {
    before = parseToml(stripBom(text));
  } catch {
    return { ok: false, reason: "agent.toml is not valid TOML. zycode does not edit a file it cannot read." };
  }
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const quoted = `"${level}"`;
  let edited: string[] | undefined;

  if (Object.hasOwn(before, "thinking_level")) {
    const at = lines.flatMap((line, i) => (LEVEL_LINE.test(stripBom(line)) ? [i] : []));
    if (at.length !== 1) {
      return { ok: false, reason: "thinking_level in agent.toml is written in a form that zycode does not edit. Change the line by hand." };
    }
    edited = [...lines];
    const line = lines[at[0] as number] as string;
    edited[at[0] as number] = line.replace(/^(\s*thinking_level\s*=\s*)(?:"[^"\n]*"|'[^'\n]*')/, (_all, head: string) => `${head}${quoted}`);
  } else {
    const header = firstTableHeader(lines);
    edited = [...lines];
    if (header < 0) {
      while (edited.length > 0 && edited[edited.length - 1] === "") edited.pop();
      edited.push(`thinking_level = ${quoted}`, "");
    } else {
      let insertAt = header;
      while (insertAt > 0 && COMMENT.test(lines[insertAt - 1] as string)) insertAt--;
      edited.splice(insertAt, 0, `thinking_level = ${quoted}`, "");
    }
  }

  const next = edited.join(eol);
  let after: Record<string, unknown>;
  try {
    after = parseToml(stripBom(next));
  } catch {
    return { ok: false, reason: "zycode could not edit agent.toml safely. Add the line thinking_level by hand." };
  }
  const expected = { ...before, thinking_level: level };
  if (!sameValue(after, expected)) {
    return { ok: false, reason: "zycode could not edit agent.toml safely. Add the line thinking_level by hand." };
  }
  return { ok: true, text: next };
}

export type ChangeResult = { ok: true } | { ok: false; reason: string };

/**
 * Changes `thinking_level` in the file. The file is read again, edited, written beside the file and
 * moved over it (a symbolic link is followed, the mode is kept), so a failure leaves the old file.
 */
export function writeThinkingLevel(file: string, level: ThinkingLevel): ChangeResult {
  if (!existsSync(file)) {
    return { ok: false, reason: "agent.toml does not exist yet. Run the setup first: it creates the file." };
  }
  let target: string;
  let text: string;
  let mode: number;
  try {
    target = realpathSync(file);
    text = readFileSync(target, "utf8");
    mode = statSync(target).mode & 0o777;
  } catch (error) {
    return { ok: false, reason: `agent.toml cannot be read: ${error instanceof Error ? error.message : String(error)}` };
  }
  const edit = setThinkingLevelInText(text, level);
  if (!edit.ok) return edit;
  let temp: string | undefined;
  try {
    const dir = mkdtempSync(path.join(path.dirname(target), ".zycode-"));
    temp = path.join(dir, "agent.toml");
    writeFileSync(temp, edit.text, { encoding: "utf8", mode });
    renameSync(temp, target);
    rmSync(dir, { recursive: true, force: true });
  } catch (error) {
    if (temp !== undefined) rmSync(path.dirname(temp), { recursive: true, force: true });
    return { ok: false, reason: `agent.toml cannot be written: ${error instanceof Error ? error.message : String(error)}` };
  }
  return { ok: true };
}
