import { describe, expect, it } from "vitest";

import { displayTitle, listMessage, normalizeFolder, rowsFromPages, sameFolder, timeAgo } from "../src/core/session-list.js";

const item = (sessionId: string, cwd: string, extra: Record<string, unknown> = {}) => ({ sessionId, cwd, title: `t-${sessionId}`, updatedAt: "2026-11-01T10:00:00Z", ...extra });

describe("sameFolder", () => {
  it("ignores a trailing separator", () => {
    expect(sameFolder("/work/app/", "/work/app", "linux")).toBe(true);
    expect(sameFolder("/work/app", "/work/app2", "linux")).toBe(false);
  });
  it("is case sensitive on Linux and not on Windows", () => {
    expect(sameFolder("/Work/App", "/work/app", "linux")).toBe(false);
    expect(sameFolder("C:\\Work\\App", "c:/work/app/", "win32")).toBe(true);
  });
  it("keeps the root", () => {
    expect(normalizeFolder("/", "linux")).toBe("/");
    expect(normalizeFolder("C:\\", "win32")).toBe("c:\\");
  });
});

describe("rowsFromPages", () => {
  it("keeps the order of the agent and joins the pages", () => {
    const rows = rowsFromPages([[item("a", "/p"), item("b", "/p")], [item("c", "/p")]], "/p", "linux");
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
  it("drops a session of another folder", () => {
    const rows = rowsFromPages([[item("a", "/p"), item("x", "/other"), item("y", "/p/sub")]], "/p", "linux");
    expect(rows.map((r) => r.id)).toEqual(["a"]);
  });
  it("drops a row without an id or a folder, and the second row with the same id", () => {
    const rows = rowsFromPages([[item("a", "/p"), item("a", "/p"), { sessionId: "", cwd: "/p" }, { sessionId: "n" }, { cwd: "/p" }]], "/p", "linux");
    expect(rows.map((r) => r.id)).toEqual(["a"]);
  });
  it("reads a time that is not a date as no time", () => {
    const [row] = rowsFromPages([[item("a", "/p", { updatedAt: "soon" })]], "/p", "linux");
    expect(row?.updatedAt).toBeUndefined();
  });
});

describe("displayTitle", () => {
  it("makes one line", () => {
    expect(displayTitle({ id: "abcdef012345", title: "  fix\n the   bug " })).toBe("fix the bug");
  });
  it("cuts a long title", () => {
    const title = displayTitle({ id: "a", title: "x".repeat(300) });
    expect(title).toHaveLength(120);
    expect(title.endsWith("…")).toBe(true);
  });
  it("uses the short id when there is no title", () => {
    expect(displayTitle({ id: "abcdef0123456789", title: " " })).toBe("Chat abcdef01");
  });
});

describe("timeAgo", () => {
  const now = Date.parse("2026-11-12T12:00:00Z");
  const ago = (ms: number) => new Date(now - ms).toISOString();
  it("uses the wording of the pager", () => {
    expect(timeAgo(ago(10_000), now)).toBe("just now");
    expect(timeAgo(ago(5 * 60_000), now)).toBe("5m ago");
    expect(timeAgo(ago(3 * 3600_000), now)).toBe("3h ago");
    expect(timeAgo(ago(2 * 86400_000), now)).toBe("2d ago");
    expect(timeAgo(ago(95 * 86400_000), now)).toBe("3mo ago");
  });
  it("says nothing when there is no time", () => {
    expect(timeAgo(undefined, now)).toBe("");
  });
});

describe("listMessage", () => {
  it("names each state with no rows", () => {
    expect(listMessage({ kind: "empty" })).toMatch(/No past chats/);
    expect(listMessage({ kind: "error", message: "boom" })).toBe("The list could not be read: boom");
    expect(listMessage({ kind: "unavailable", reason: "r" })).toBe("r");
    expect(listMessage({ kind: "loading", rows: [] })).toMatch(/Reading/);
    expect(listMessage({ kind: "idle" })).toMatch(/Start zycode/);
  });
  it("says nothing for a full list and warns when it is cut", () => {
    expect(listMessage({ kind: "ready", rows: [], truncated: false })).toBeUndefined();
    expect(listMessage({ kind: "ready", rows: [{ id: "a", title: "", updatedAt: undefined, cwd: "/p" }], truncated: true })).toMatch(/newest 1 chats/);
  });
});
