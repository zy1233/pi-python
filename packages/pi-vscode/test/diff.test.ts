import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { DiffStore } from "../src/core/diff-store.js";
import { applyEdits, parsePatch, readEdits, reversePatch } from "../src/core/diff.js";

// Patches made by Python's difflib in the form of pi_agent_core's `generate_unified_patch` (n=4).
const patches = JSON.parse(readFileSync(new URL("./fixtures/patches.json", import.meta.url), "utf8")) as {
  old: string;
  new: string;
  patch: string;
}[];

describe("reversePatch", () => {
  it.each(patches.map((p, i) => [i, p] as const))("restores the old file of patch %i", (_i, p) => {
    expect(reversePatch(p.new, p.patch)).toBe(p.old);
  });

  it("fails when the file changed after the call", () => {
    const p = patches[0]!;
    expect(reversePatch(p.new.replace("line five", "line 5 (changed by hand)"), p.patch)).toBeUndefined();
  });

  it("fails on text that is not a patch", () => {
    expect(reversePatch("x", "just text")).toBeUndefined();
  });

  it("finds a hunk that moved because lines were added above it", () => {
    const p = patches[0]!;
    expect(reversePatch("new top line\n" + p.new, p.patch)).toBe("new top line\n" + p.old);
  });

  it("reads CRLF input as LF", () => {
    const p = patches[1]!;
    expect(reversePatch(p.new.replace(/\n/g, "\r\n"), p.patch)).toBe(p.old);
  });
});

describe("parsePatch", () => {
  it("skips the file headers and reads the new start of each hunk", () => {
    const hunks = parsePatch(patches[1]!.patch);
    expect(hunks).toHaveLength(1);
    expect(hunks[0]).toMatchObject({ newStart: 1, oldLines: ["one", "two", "three"], newLines: ["one", "TWO", "three", "four"] });
  });
});

describe("applyEdits", () => {
  it("applies replacements that match once, against the original", () => {
    expect(
      applyEdits("a\nb\nc\n", [
        { oldText: "a", newText: "A" },
        { oldText: "c", newText: "C" },
      ]),
    ).toBe("A\nb\nC\n");
  });

  it("matches against the original, not step by step", () => {
    expect(
      applyEdits("x y", [
        { oldText: "x", newText: "y" },
        { oldText: "y", newText: "z" },
      ]),
    ).toBe("y z");
  });

  it("fails for no match, two matches, an empty oldText and overlapping edits", () => {
    expect(applyEdits("abc", [{ oldText: "zzz", newText: "" }])).toBeUndefined();
    expect(applyEdits("aa", [{ oldText: "a", newText: "b" }])).toBeUndefined();
    expect(applyEdits("abc", [{ oldText: "", newText: "b" }])).toBeUndefined();
    expect(
      applyEdits("abcd", [
        { oldText: "abc", newText: "x" },
        { oldText: "bcd", newText: "y" },
      ]),
    ).toBeUndefined();
  });
});

describe("readEdits", () => {
  it("accepts the edit tool's input and refuses other shapes", () => {
    expect(readEdits({ path: "a", edits: [{ oldText: "a", newText: "b" }] })).toEqual([{ oldText: "a", newText: "b" }]);
    expect(readEdits({ path: "a", edits: [] })).toBeUndefined();
    expect(readEdits({ path: "a", edits: [{ oldText: 1 }] })).toBeUndefined();
    expect(readEdits(null)).toBeUndefined();
    expect(readEdits({ path: "a", content: "x" })).toBeUndefined();
  });
});

describe("DiffStore", () => {
  it("returns what it holds", () => {
    const store = new DiffStore();
    store.put("a", "old", "new");
    expect(store.get("a", "before")).toBe("old");
    expect(store.get("a", "after")).toBe("new");
    expect(store.get("b", "after")).toBeUndefined();
  });

  it("drops the oldest entry past the count limit", () => {
    const store = new DiffStore({ maxEntries: 2, maxBytes: 1000 });
    store.put("a", "1", "1");
    store.put("b", "2", "2");
    store.put("c", "3", "3");
    expect(store.has("a")).toBe(false);
    expect(store.has("b") && store.has("c")).toBe(true);
  });

  it("drops the oldest entries past the byte limit but keeps the newest one", () => {
    const store = new DiffStore({ maxEntries: 10, maxBytes: 10 });
    store.put("a", "xxxx", "xxxx");
    store.put("b", "yyyyyyyyyyyy", "yyyyyyyyyyyy");
    expect(store.has("a")).toBe(false);
    expect(store.has("b")).toBe(true);
    expect(store.size).toBe(1);
  });

  it("replaces an entry with the same key and counts its bytes once", () => {
    const store = new DiffStore();
    store.put("a", "12345", "12345");
    store.put("a", "1", "1");
    expect(store.totalBytes).toBe(2);
    expect(store.size).toBe(1);
  });
});
