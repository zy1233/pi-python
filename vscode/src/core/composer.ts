import { fuzzyScore } from "./mentions.js";
import type { NoticeLevel, StateMessage, Status, WebviewMessage } from "./protocol.js";

export interface SlashCommandInfo {
  name: string;
  description: string;
  inputHint?: string;
}

export interface ComposerCommand {
  name: string;
  description: string;
  source: "agent" | "client";
  takesArgs: boolean;
  argHint?: string;
}

export type SlashArgCommand = "mode" | "thinking" | "model";

export type ComposerTrigger =
  | { kind: "slash"; query: string; range: { start: number; end: number } }
  | { kind: "slash-arg"; command: SlashArgCommand; query: string; range: { start: number; end: number } }
  | { kind: "mention"; query: string; range: { start: number; end: number } };

export interface SlashArgOption {
  id: string;
  label: string;
  description?: string;
  current?: boolean;
}

export type ClientCommandDispatch =
  | { kind: "message"; message: WebviewMessage }
  | { kind: "notice"; level: NoticeLevel; text: string };

const MAX_COMMAND_NAME_CHARS = 128;
const MAX_COMMAND_DESC_CHARS = 512;
const MAX_COMMAND_HINT_CHARS = 128;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseAvailableCommandsUpdate(raw: unknown): SlashCommandInfo[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const deduped = new Map<string, SlashCommandInfo>();
  for (const entry of raw) {
    if (!isRecord(entry) || typeof entry.name !== "string") continue;
    const name = entry.name.trim().replace(/^\/+/, "");
    if (name === "" || name.length > MAX_COMMAND_NAME_CHARS || /\s/.test(name)) continue;
    const description =
      typeof entry.description === "string" ? entry.description.trim().slice(0, MAX_COMMAND_DESC_CHARS) : "";
    let inputHint: string | undefined;
    if (isRecord(entry.input) && typeof entry.input.hint === "string") {
      inputHint = entry.input.hint.trim().slice(0, MAX_COMMAND_HINT_CHARS);
    }
    const info: SlashCommandInfo = {
      name,
      description,
      ...(inputHint !== undefined ? { inputHint } : {}),
    };
    const key = name.toLowerCase();
    deduped.delete(key);
    deduped.set(key, info);
  }
  return [...deduped.values()];
}

export function sameSlashCommands(a: readonly SlashCommandInfo[], b: readonly SlashCommandInfo[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const left = a[i];
    const right = b[i];
    if (!left || !right) return false;
    if (left.name !== right.name || left.description !== right.description || left.inputHint !== right.inputHint) {
      return false;
    }
  }
  return true;
}

type CatalogState = Pick<StateMessage, "commands" | "mode" | "thinking" | "models">;

export function buildCommandCatalog(state: CatalogState): ComposerCommand[] {
  const out: ComposerCommand[] = [];
  const seen = new Set<string>();

  for (const cmd of state.commands ?? []) {
    seen.add(cmd.name.toLowerCase());
    out.push({
      name: cmd.name,
      description: cmd.description,
      source: "agent",
      takesArgs: cmd.inputHint !== undefined,
      ...(cmd.inputHint !== undefined ? { argHint: cmd.inputHint } : {}),
    });
  }

  const clientCommands: ComposerCommand[] = [{ name: "new", description: "Start a new chat", source: "client", takesArgs: false }];
  if (state.mode && state.mode.options.length > 0) {
    clientCommands.push({
      name: "mode",
      description: "Switch permission mode",
      source: "client",
      takesArgs: true,
      argHint: state.mode.options.map((o) => o.id).join(" | "),
    });
  }
  if (state.thinking && state.thinking.disabled === undefined) {
    clientCommands.push({
      name: "thinking",
      description: "Set thinking level in agent.toml",
      source: "client",
      takesArgs: true,
      argHint: "<level>",
    });
  }
  if (state.models && state.models.options.length > 0) {
    clientCommands.push({
      name: "model",
      description: "Switch model for this session",
      source: "client",
      takesArgs: true,
      argHint: "<model>",
    });
  }
  clientCommands.push(
    { name: "restart", description: "Restart the zycode agent", source: "client", takesArgs: false },
    { name: "setup", description: "Set up model and API key", source: "client", takesArgs: false },
    { name: "config", description: "Open agent.toml", source: "client", takesArgs: false },
    { name: "log", description: "Show zycode output log", source: "client", takesArgs: false },
    { name: "guide", description: "Open the getting-started guide", source: "client", takesArgs: false },
  );

  for (const cmd of clientCommands) {
    if (!seen.has(cmd.name.toLowerCase())) {
      out.push(cmd);
    }
  }
  return out;
}

function scanTokenEnd(text: string, from: number, stopPattern: RegExp): number {
  let end = from;
  while (end < text.length && !stopPattern.test(text[end] ?? "")) {
    end++;
  }
  return end;
}

function scanMentionTokenEnd(text: string, pos: number): number {
  let end = pos;
  while (end < text.length && /[A-Za-z0-9_./\\:-]/.test(text[end] ?? "")) {
    end++;
  }
  while (end > pos && (text[end - 1] === "." || text[end - 1] === "-" || text[end - 1] === ":")) {
    end--;
  }
  return end;
}

export function detectComposerTrigger(text: string, cursor: number): ComposerTrigger | undefined {
  const pos = Math.max(0, Math.min(cursor, text.length));
  const before = text.slice(0, pos);

  if (!before.includes("\n")) {
    const slashMatch = /^[ \t]*\/([^\s]*)$/.exec(before);
    if (slashMatch) {
      const slashIdx = before.indexOf("/");
      const end = scanTokenEnd(text, pos, /\s/);
      return {
        kind: "slash",
        query: slashMatch[1] ?? "",
        range: { start: slashIdx, end },
      };
    }

    const argMatch = /^[ \t]*\/([A-Za-z0-9_-]+)[ \t]+([^\s]*)$/.exec(before);
    if (argMatch) {
      const cmd = (argMatch[1] ?? "").toLowerCase();
      if ((cmd === "mode" || cmd === "thinking" || cmd === "model") && !/\s\S/.test(text.slice(pos))) {
        const argQuery = argMatch[2] ?? "";
        const argStart = pos - argQuery.length;
        const end = scanTokenEnd(text, pos, /\s/);
        return {
          kind: "slash-arg",
          command: cmd,
          query: argQuery,
          range: { start: argStart, end },
        };
      }
    }
  }

  const atIdx = before.lastIndexOf("@");
  if (atIdx < 0) return undefined;
  if (atIdx > 0 && /[A-Za-z0-9_]/.test(before[atIdx - 1] ?? "")) {
    return undefined;
  }
  const query = before.slice(atIdx + 1);
  if (/[\s,;]/.test(query)) {
    return undefined;
  }
  const end = scanMentionTokenEnd(text, pos);
  return {
    kind: "mention",
    query,
    range: { start: atIdx, end },
  };
}

export function rankCommands(
  commands: readonly ComposerCommand[],
  query: string,
): { command: ComposerCommand; nameMatches?: number[] }[] {
  const trimmed = query.trim();
  if (trimmed === "") {
    return commands.map((command) => ({ command }));
  }

  const scored: { command: ComposerCommand; nameMatches?: number[]; score: number }[] = [];
  for (const command of commands) {
    const nameHit = fuzzyScore(trimmed, command.name);
    const descHit = fuzzyScore(trimmed, command.description);
    if (!nameHit && !descHit) continue;
    const score = Math.max(nameHit ? nameHit.score + 200 : -Infinity, descHit ? descHit.score : -Infinity);
    scored.push({
      command,
      ...(nameHit && nameHit.indices.length > 0 ? { nameMatches: nameHit.indices } : {}),
      score,
    });
  }

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.command.name.localeCompare(b.command.name);
  });

  return scored.map(({ command, nameMatches }) => ({
    command,
    ...(nameMatches ? { nameMatches } : {}),
  }));
}

export function slashArgOptions(
  command: SlashArgCommand,
  state: Pick<StateMessage, "mode" | "thinking" | "models">,
  query: string,
): { option: SlashArgOption; labelMatches?: number[] }[] {
  let options: SlashArgOption[];
  switch (command) {
    case "mode":
      options = (state.mode?.options ?? []).map((o) => ({
        id: o.id,
        label: o.name,
        ...(o.id === state.mode?.current ? { current: true } : {}),
      }));
      break;
    case "thinking":
      options =
        state.thinking && state.thinking.disabled === undefined
          ? state.thinking.levels.map((level) => ({
              id: level.id,
              label: level.name,
              description: level.description,
              ...(level.id === state.thinking?.current ? { current: true } : {}),
            }))
          : [];
      break;
    case "model":
      options = (state.models?.options ?? []).map((o) => ({
        id: o.id,
        label: o.name,
        ...(o.name !== o.id ? { description: o.id } : {}),
        ...(o.id === state.models?.current ? { current: true } : {}),
      }));
      break;
  }

  const trimmed = query.trim();
  if (trimmed === "") {
    return options.map((option) => ({ option }));
  }

  const scored: { option: SlashArgOption; labelMatches?: number[]; score: number }[] = [];
  for (const option of options) {
    const labelHit = fuzzyScore(trimmed, option.label);
    const idHit = fuzzyScore(trimmed, option.id);
    if (!labelHit && !idHit) continue;
    const score = Math.max(labelHit ? labelHit.score + 50 : -Infinity, idHit ? idHit.score : -Infinity);
    scored.push({
      option,
      ...(labelHit && labelHit.indices.length > 0 ? { labelMatches: labelHit.indices } : {}),
      score,
    });
  }

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.option.label.localeCompare(b.option.label);
  });

  return scored.map(({ option, labelMatches }) => ({
    option,
    ...(labelMatches ? { labelMatches } : {}),
  }));
}

const SIMPLE_CLIENT_COMMANDS: Record<string, WebviewMessage> = {
  new: { type: "newChat" },
  restart: { type: "restartAgent" },
  setup: { type: "runSetup" },
  config: { type: "openAgentConfig" },
  log: { type: "openLog" },
  guide: { type: "openGuide" },
};

export function resolveClientSlashCommand(
  text: string,
  state: CatalogState,
): ClientCommandDispatch | undefined {
  const trimmed = text.trim();
  if (!trimmed.startsWith("/") || trimmed.includes("\n")) return undefined;

  const match = /^\/([^\s]+)(?:\s+([\s\S]*))?$/.exec(trimmed);
  if (!match) return undefined;

  const name = (match[1] ?? "").toLowerCase();
  const arg = (match[2] ?? "").trim();

  if (state.commands?.some((c) => c.name.toLowerCase() === name)) {
    return undefined;
  }

  const simpleMessage = SIMPLE_CLIENT_COMMANDS[name];
  if (simpleMessage) {
    if (arg !== "") {
      return { kind: "notice", level: "info", text: `/${name} takes no arguments.` };
    }
    return { kind: "message", message: simpleMessage };
  }

  if (name === "mode") {
    const options = state.mode?.options ?? [];
    if (!state.mode || options.length === 0) {
      return { kind: "notice", level: "warning", text: "Permission mode cannot be changed right now." };
    }
    const available = options.map((o) => o.id).join(", ");
    if (arg === "") {
      return { kind: "notice", level: "info", text: `Current permission mode: ${state.mode.current}. Usage: /mode <${available}>` };
    }
    const found = options.find((o) => o.id.toLowerCase() === arg.toLowerCase() || o.name.toLowerCase() === arg.toLowerCase());
    if (found && (found.id === "ask" || found.id === "auto" || found.id === "always-approve")) {
      return { kind: "message", message: { type: "setMode", value: found.id } };
    }
    return { kind: "notice", level: "warning", text: `Unknown permission mode "${arg}". Available: ${available}.` };
  }

  if (name === "thinking") {
    if (!state.thinking) {
      return { kind: "notice", level: "warning", text: "Thinking level is not available while the agent is stopped." };
    }
    if (state.thinking.disabled !== undefined) {
      return { kind: "notice", level: "info", text: `The thinking level cannot be changed now: ${state.thinking.disabled}` };
    }
    const levels = state.thinking.levels;
    const available = levels.map((l) => l.id).join(", ");
    if (arg === "") {
      return {
        kind: "notice",
        level: "info",
        text: `Current thinking level: ${state.thinking.current ?? "unknown"}. Usage: /thinking <${available}>`,
      };
    }
    const found = levels.find((l) => l.id.toLowerCase() === arg.toLowerCase() || l.name.toLowerCase() === arg.toLowerCase());
    if (found) {
      return { kind: "message", message: { type: "setThinking", value: found.id } };
    }
    return { kind: "notice", level: "warning", text: `Unknown thinking level "${arg}". Available: ${available}.` };
  }

  if (name === "model") {
    const options = state.models?.options ?? [];
    if (!state.models || options.length === 0) {
      return { kind: "notice", level: "warning", text: "No selectable models are available from the agent." };
    }
    const available = options.map((o) => o.id).join(", ");
    if (arg === "") {
      return { kind: "notice", level: "info", text: `Current model: ${state.models.current}. Usage: /model <${available}>` };
    }
    const found = options.find((o) => o.id.toLowerCase() === arg.toLowerCase() || o.name.toLowerCase() === arg.toLowerCase());
    if (found) {
      return { kind: "message", message: { type: "setModel", value: found.id } };
    }
    return { kind: "notice", level: "warning", text: `Unknown model "${arg}". Available: ${available}.` };
  }

  return undefined;
}

/** Whether a local client command message can run while the controller is in `status`. */
export function canRunClientCommandInStatus(message: WebviewMessage, status: Status): boolean {
  switch (message.type) {
    case "openLog":
    case "openAgentConfig":
    case "openGuide":
      return true;
    case "restartAgent":
      return status !== "starting";
    case "runSetup":
    case "newChat":
    case "setMode":
    case "setThinking":
    case "setModel":
      return status === "ready";
    default:
      return status === "ready";
  }
}
