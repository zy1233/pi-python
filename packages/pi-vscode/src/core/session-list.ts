/**
 * The list of past sessions (docs/specs/2026-11-04-vscode-extension-design.md, section 7b). The agent
 * is the only source: `session/list` of the folder, as the pager reads it (`acp/session_list.rs`,
 * `session_picker_entries_from_acp`). The extension reads no session file and does not sort again:
 * the order is the agent's (newest first), and the row is the title and the time of the last write.
 */

export interface SessionRow {
  id: string;
  /** The first prompt of the session, as the agent gives it (one line). */
  title: string;
  /** ISO time of the last write, when the agent gives one. */
  updatedAt: string | undefined;
  cwd: string;
}

export interface RawListedSession {
  sessionId?: unknown;
  cwd?: unknown;
  title?: unknown;
  updatedAt?: unknown;
}

export type SessionListState =
  | { kind: "idle" }
  | { kind: "loading"; rows: SessionRow[] }
  | { kind: "ready"; rows: SessionRow[]; truncated: boolean }
  | { kind: "empty" }
  | { kind: "error"; message: string }
  | { kind: "unavailable"; reason: string };

/** The pager follows at most 100 pages of 50. A panel needs fewer: the newest 1000 are enough. */
export const MAX_LIST_PAGES = 20;
export const MAX_TITLE_CHARS = 120;

/**
 * `true` when two paths name the same folder. The agent filters by the exact `cwd` text that the
 * extension sent, so this is a second check on every row (and on the folder of a session that is
 * opened): a session of another folder must not show here. It ignores a trailing separator, and on
 * Windows the letter case and the kind of separator.
 */
export function sameFolder(a: string, b: string, platform: NodeJS.Platform): boolean {
  return normalizeFolder(a, platform) === normalizeFolder(b, platform);
}

export function normalizeFolder(folder: string, platform: NodeJS.Platform): string {
  let value = folder;
  if (platform === "win32") value = value.replace(/\//g, "\\").toLowerCase();
  const separator = platform === "win32" ? "\\" : "/";
  while (value.length > 1 && value.endsWith(separator) && !/^[a-z]:\\$/.test(value)) value = value.slice(0, -1);
  return value;
}

/** The rows of the pages that the agent gave, for one folder. Order and duplicates: the first of an id wins. */
export function rowsFromPages(pages: RawListedSession[][], folder: string, platform: NodeJS.Platform): SessionRow[] {
  const seen = new Set<string>();
  const rows: SessionRow[] = [];
  for (const page of pages) {
    for (const item of page) {
      if (typeof item.sessionId !== "string" || item.sessionId === "" || seen.has(item.sessionId)) continue;
      if (typeof item.cwd !== "string" || !sameFolder(item.cwd, folder, platform)) continue;
      seen.add(item.sessionId);
      rows.push({
        id: item.sessionId,
        title: typeof item.title === "string" ? item.title : "",
        updatedAt: typeof item.updatedAt === "string" && !Number.isNaN(Date.parse(item.updatedAt)) ? item.updatedAt : undefined,
        cwd: item.cwd,
      });
    }
  }
  return rows;
}

/** The title of a row: one line, no longer than `MAX_TITLE_CHARS`; the short id when the agent gave no title. */
export function displayTitle(row: Pick<SessionRow, "id" | "title">): string {
  const line = row.title.replace(/\s+/g, " ").trim();
  if (line === "") return `Chat ${row.id.slice(0, 8)}`;
  return line.length > MAX_TITLE_CHARS ? `${line.slice(0, MAX_TITLE_CHARS - 1)}…` : line;
}

/** The pager's wording (`format_time_ago`): just now, 5m ago, 3h ago, 2d ago, 4mo ago. */
export function timeAgo(iso: string | undefined, now: number): string {
  if (iso === undefined) return "";
  const minutes = Math.floor((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/** What the panel says when there is no row to show. */
export function listMessage(state: SessionListState): string | undefined {
  switch (state.kind) {
    case "idle":
      return "Start zycode to see the past chats of this folder.";
    case "empty":
      return "No past chats in this folder yet. A chat appears here after its first message.";
    case "error":
      return `The list could not be read: ${state.message}`;
    case "unavailable":
      return state.reason;
    case "ready":
      return state.truncated ? `Showing the newest ${state.rows.length} chats.` : undefined;
    case "loading":
      return state.rows.length === 0 ? "Reading the past chats…" : undefined;
  }
}
