/**
 * What the status row says: the context window, the cost, the clocks, the folder
 * (docs/specs/2026-11-04-vscode-extension-design.md, section 7d). It has no `vscode` import: the
 * chat view and the status bar item both use it.
 *
 * The extension does not count tokens and does not know a price. Every number here is one that the
 * agent sent (`usage_update`) or one that the host measured with its own clock.
 */
import type { StateMessage, UsageState } from "./protocol.js";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/**
 * The numbers of an ACP `usage_update`: `used`, `size` and `cost`. It gives `undefined` when `used`
 * is not a count, because such an update has nothing that the view can show. A `size` that is not
 * above zero is left out (a percent of nothing is not a number). A `cost` that is not an amount with
 * a currency is left out too.
 */
export function readUsageUpdate(update: Record<string, unknown>): UsageState | undefined {
  if (!isCount(update.used)) return undefined;
  const usage: UsageState = { used: Math.round(update.used) };
  if (isCount(update.size) && update.size > 0) usage.size = Math.round(update.size);
  const cost = update.cost;
  if (isRecord(cost) && isCount(cost.amount) && typeof cost.currency === "string" && cost.currency.trim() !== "") {
    usage.cost = { amount: cost.amount, currency: cost.currency.trim().slice(0, 16) };
  }
  return usage;
}

export function sameUsage(a: UsageState | undefined, b: UsageState | undefined): boolean {
  if (a === undefined || b === undefined) return a === b;
  return a.used === b.used && a.size === b.size && a.cost?.amount === b.cost?.amount && a.cost?.currency === b.cost?.currency;
}

function trimZeros(text: string): string {
  return text.includes(".") ? text.replace(/\.?0+$/, "") : text;
}

/** A count of tokens in a few characters: `950`, `54.2k`, `128k`, `1.05M`. */
export function formatTokens(count: number): string {
  const n = Math.max(0, Math.round(count));
  if (n < 1000) return String(n);
  const thousands = n / 1000;
  if (thousands < 999.5) return `${trimZeros(thousands < 100 ? thousands.toFixed(1) : thousands.toFixed(0))}k`;
  const millions = n / 1_000_000;
  return `${trimZeros(millions < 10 ? millions.toFixed(2) : millions < 100 ? millions.toFixed(1) : millions.toFixed(0))}M`;
}

/** A count of tokens in full, with separators (`54,200`). The format does not depend on the locale. */
export function formatCount(count: number): string {
  return Math.max(0, Math.round(count)).toLocaleString("en-US");
}

/** The percent from which the context row shows a warning, and a danger. They change the colour only. */
export const WARN_PERCENT = 80;
export const DANGER_PERCENT = 90;

export type ContextLevel = "ok" | "warn" | "danger";

/** The percent as the row shows it: `0%`, `<1%` or a whole number. */
export function formatPercent(percent: number): string {
  if (!(percent > 0)) return "0%";
  if (percent < 1) return "<1%";
  return `${Math.round(percent)}%`;
}

/** The level of the shown (rounded) percent, so that the text and the colour never disagree. */
export function contextLevel(percent: number): ContextLevel {
  const shown = Math.round(percent);
  return shown >= DANGER_PERCENT ? "danger" : shown >= WARN_PERCENT ? "warn" : "ok";
}

export interface ContextView {
  /** Percent of the window that is used. Absent when the agent gave no usable size. */
  percent?: number;
  level?: ContextLevel;
  /** `42%`, or `54.2k tokens` when the size is not known. */
  short: string;
  /** `54.2k of 128k tokens`. Empty when the size is not known (`short` has the count then). */
  count: string;
  /** For a tooltip: the same in full numbers. */
  title: string;
}

export function describeContext(usage: UsageState): ContextView {
  if (usage.size === undefined) {
    return {
      short: `${formatTokens(usage.used)} tokens`,
      count: "",
      title: `${formatCount(usage.used)} tokens are in the context. The agent did not say how large the window is.`,
    };
  }
  const percent = (usage.used / usage.size) * 100;
  return {
    percent,
    level: contextLevel(percent),
    short: formatPercent(percent),
    count: `${formatTokens(usage.used)} of ${formatTokens(usage.size)} tokens`,
    title: `${formatCount(usage.used)} of ${formatCount(usage.size)} tokens are in the context (${formatPercent(percent)}). The agent counts them.`,
  };
}

/** `$0.12`. A small amount keeps four decimals (`$0.0032`). A currency that `Intl` rejects is written as it came. */
export function formatCost(cost: { amount: number; currency: string }): string {
  const maximumFractionDigits = cost.amount > 0 && cost.amount < 0.1 ? 4 : 2;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: cost.currency,
      minimumFractionDigits: 2,
      maximumFractionDigits,
    }).format(cost.amount);
  } catch {
    return `${Number(cost.amount.toFixed(4))} ${cost.currency}`;
  }
}

/**
 * `8 s`, `1 min 05 s`, `1 h 02 min`. With `precise`, a time under 10 s keeps one decimal (`8.4 s`):
 * that is for a turn that has ended. A clock that runs shows whole seconds.
 */
export function formatDuration(ms: number, precise = false): string {
  if (!Number.isFinite(ms) || ms < 0) return "0 s";
  if (precise && ms < 10_000) return `${(Math.floor(ms / 100) / 10).toFixed(1)} s`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${String(seconds % 60).padStart(2, "0")} s`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** The last part of a folder path, for either kind of separator. */
export function folderName(folder: string): string {
  const parts = folder.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || folder;
}

/** The text that the status bar item adds after `zycode: <state>`: ` · context 42%`. Empty when the agent said nothing. */
export function contextBadge(usage: UsageState | undefined): string {
  return usage ? ` · context ${describeContext(usage).short}` : "";
}

/** The lines of the status bar tooltip that come from the status row. A line is there only when its number is. */
export function statusRowLines(state: Pick<StateMessage, "usage" | "turn" | "cwd">): string[] {
  const lines: string[] = [];
  if (state.usage) {
    const view = describeContext(state.usage);
    lines.push(`Context: ${view.short}${view.count ? ` (${view.count})` : ""}`);
    if (state.usage.cost) lines.push(`Cost: ${formatCost(state.usage.cost)}`);
  }
  if (state.turn?.lastMs !== undefined) lines.push(`Last turn: ${formatDuration(state.turn.lastMs, true)}`);
  if (state.cwd) lines.push(`Folder: ${state.cwd}`);
  return lines;
}
