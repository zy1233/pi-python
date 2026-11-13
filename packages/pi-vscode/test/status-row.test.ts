import { describe, expect, it } from "vitest";

import {
  contextBadge,
  contextLevel,
  describeContext,
  folderName,
  formatCost,
  formatCount,
  formatDuration,
  formatPercent,
  formatTokens,
  readUsageUpdate,
  sameUsage,
  statusRowLines,
} from "../src/core/status-row.js";

describe("readUsageUpdate", () => {
  it("reads used, size and cost of an ACP usage_update", () => {
    expect(readUsageUpdate({ sessionUpdate: "usage_update", used: 54200, size: 128000, cost: { amount: 0.12, currency: "USD" } })).toEqual({
      used: 54200,
      size: 128000,
      cost: { amount: 0.12, currency: "USD" },
    });
  });

  it("keeps a count that has no size", () => {
    expect(readUsageUpdate({ used: 10 })).toEqual({ used: 10 });
  });

  it("leaves out a size that is not above zero or not a number: a percent of nothing is not a number", () => {
    expect(readUsageUpdate({ used: 10, size: 0 })).toEqual({ used: 10 });
    expect(readUsageUpdate({ used: 10, size: -5 })).toEqual({ used: 10 });
    expect(readUsageUpdate({ used: 10, size: "big" })).toEqual({ used: 10 });
    expect(readUsageUpdate({ used: 10, size: null })).toEqual({ used: 10 });
    expect(readUsageUpdate({ used: 10, size: Number.NaN })).toEqual({ used: 10 });
  });

  it("gives nothing when used is not a count", () => {
    expect(readUsageUpdate({})).toBeUndefined();
    expect(readUsageUpdate({ size: 100 })).toBeUndefined();
    expect(readUsageUpdate({ used: "10" })).toBeUndefined();
    expect(readUsageUpdate({ used: -1 })).toBeUndefined();
    expect(readUsageUpdate({ used: Number.NaN })).toBeUndefined();
    expect(readUsageUpdate({ used: Number.POSITIVE_INFINITY })).toBeUndefined();
    expect(readUsageUpdate({ used: null })).toBeUndefined();
  });

  it("leaves out a cost that is not an amount with a currency", () => {
    expect(readUsageUpdate({ used: 1, cost: { amount: "1", currency: "USD" } })).toEqual({ used: 1 });
    expect(readUsageUpdate({ used: 1, cost: { amount: 1 } })).toEqual({ used: 1 });
    expect(readUsageUpdate({ used: 1, cost: { amount: 1, currency: "  " } })).toEqual({ used: 1 });
    expect(readUsageUpdate({ used: 1, cost: { amount: -1, currency: "USD" } })).toEqual({ used: 1 });
    expect(readUsageUpdate({ used: 1, cost: "free" })).toEqual({ used: 1 });
    expect(readUsageUpdate({ used: 1, cost: [1, "USD"] })).toEqual({ used: 1 });
  });

  it("rounds a count, and trims and caps the currency", () => {
    expect(readUsageUpdate({ used: 10.6, size: 99.5, cost: { amount: 1, currency: " USD " } })).toEqual({
      used: 11,
      size: 100,
      cost: { amount: 1, currency: "USD" },
    });
    expect(readUsageUpdate({ used: 1, cost: { amount: 1, currency: "x".repeat(40) } })?.cost?.currency).toHaveLength(16);
  });
});

describe("sameUsage", () => {
  it("compares every number", () => {
    const a = { used: 1, size: 2, cost: { amount: 0.5, currency: "USD" } };
    expect(sameUsage(a, { ...a, cost: { ...a.cost } })).toBe(true);
    expect(sameUsage(a, { ...a, used: 2 })).toBe(false);
    expect(sameUsage(a, { ...a, size: 3 })).toBe(false);
    expect(sameUsage(a, { ...a, cost: { amount: 0.6, currency: "USD" } })).toBe(false);
    expect(sameUsage(a, { ...a, cost: { amount: 0.5, currency: "EUR" } })).toBe(false);
    expect(sameUsage(a, { used: 1, size: 2 })).toBe(false);
    expect(sameUsage(undefined, undefined)).toBe(true);
    expect(sameUsage(a, undefined)).toBe(false);
    expect(sameUsage(undefined, a)).toBe(false);
  });
});

describe("formatTokens", () => {
  it.each([
    [0, "0"],
    [950, "950"],
    [999, "999"],
    [1000, "1k"],
    [1234, "1.2k"],
    [1499.6, "1.5k"],
    [54_200, "54.2k"],
    [99_000, "99k"],
    [112_562, "113k"],
    [128_000, "128k"],
    [999_499, "999k"],
    [999_500, "1M"],
    [1_000_000, "1M"],
    [1_048_576, "1.05M"],
    [2_000_000, "2M"],
    [12_500_000, "12.5M"],
    [200_000_000, "200M"],
    [-5, "0"],
  ])("writes %d as %s", (count, text) => {
    expect(formatTokens(count)).toBe(text);
  });

  it("writes full counts with separators, whatever the locale", () => {
    expect(formatCount(54_200)).toBe("54,200");
    expect(formatCount(1_048_576)).toBe("1,048,576");
    expect(formatCount(7)).toBe("7");
  });
});

describe("percent and level", () => {
  it.each([
    [0, "0%"],
    [0.4, "<1%"],
    [1, "1%"],
    [41.6, "42%"],
    [100, "100%"],
    [112.4, "112%"],
    [Number.NaN, "0%"],
    [-3, "0%"],
  ])("writes %d as %s", (percent, text) => {
    expect(formatPercent(percent)).toBe(text);
  });

  it("takes the level from the percent that is shown, so that text and colour agree", () => {
    expect(contextLevel(0)).toBe("ok");
    expect(contextLevel(79.4)).toBe("ok"); // shown as 79%
    expect(contextLevel(79.5)).toBe("warn"); // shown as 80%
    expect(contextLevel(89.4)).toBe("warn"); // shown as 89%
    expect(contextLevel(89.5)).toBe("danger"); // shown as 90%
    expect(contextLevel(100)).toBe("danger");
    expect(contextLevel(250)).toBe("danger");
  });
});

describe("describeContext", () => {
  it("has the percent, the counts and a tooltip with full numbers when the size is known", () => {
    expect(describeContext({ used: 54_200, size: 128_000 })).toEqual({
      percent: (54_200 / 128_000) * 100,
      level: "ok",
      short: "42%",
      count: "54.2k of 128k tokens",
      title: "54,200 of 128,000 tokens are in the context (42%). The agent counts them.",
    });
  });

  it("has only the count when the size is not known, and no percent and no level", () => {
    const view = describeContext({ used: 54_200 });
    expect(view).toEqual({
      short: "54.2k tokens",
      count: "",
      title: "54,200 tokens are in the context. The agent did not say how large the window is.",
    });
    expect(view.percent).toBeUndefined();
    expect(view.level).toBeUndefined();
  });

  it("goes above 100% when the agent says that more tokens are used than the window holds", () => {
    expect(describeContext({ used: 112_500, size: 100_000 })).toMatchObject({ short: "113%", level: "danger" });
  });
});

describe("formatCost", () => {
  it("writes a currency amount", () => {
    expect(formatCost({ amount: 0.12, currency: "USD" })).toBe("$0.12");
    expect(formatCost({ amount: 1234.5, currency: "USD" })).toBe("$1,234.50");
    expect(formatCost({ amount: 0, currency: "USD" })).toBe("$0.00");
    expect(formatCost({ amount: 2, currency: "EUR" })).toBe("€2.00");
  });

  it("keeps four decimals for a small amount", () => {
    expect(formatCost({ amount: 0.0032, currency: "USD" })).toBe("$0.0032");
    expect(formatCost({ amount: 0.05, currency: "USD" })).toBe("$0.05");
  });

  it("writes a currency that Intl rejects as it came", () => {
    expect(formatCost({ amount: 0.5, currency: "dollars" })).toBe("0.5 dollars");
    expect(formatCost({ amount: 0.1 + 0.2, currency: "tokens" })).toBe("0.3 tokens");
  });
});

describe("formatDuration", () => {
  it.each([
    [0, false, "0 s"],
    [999, false, "0 s"],
    [8432, false, "8 s"],
    [59_999, false, "59 s"],
    [60_000, false, "1 min 00 s"],
    [72_000, false, "1 min 12 s"],
    [3_599_999, false, "59 min 59 s"],
    [3_600_000, false, "1 h 00 min"],
    [3_725_000, false, "1 h 02 min"],
    [36_000_000, false, "10 h 00 min"],
    [8432, true, "8.4 s"],
    [9999, true, "9.9 s"],
    [10_000, true, "10 s"],
    [0, true, "0.0 s"],
    [Number.NaN, false, "0 s"],
    [-1, true, "0 s"],
  ])("writes %d ms (precise: %s) as %s", (ms, precise, text) => {
    expect(formatDuration(ms, precise)).toBe(text);
  });
});

describe("folderName", () => {
  it("takes the last part of a path of either kind", () => {
    expect(folderName("D:\\work\\pi-python")).toBe("pi-python");
    expect(folderName("/home/u/proj/")).toBe("proj");
    expect(folderName("/home/u/proj")).toBe("proj");
    expect(folderName("proj")).toBe("proj");
    expect(folderName("C:\\")).toBe("C:");
    expect(folderName("/")).toBe("/");
  });
});

describe("the status bar item", () => {
  it("adds nothing after the state when the agent said nothing", () => {
    expect(contextBadge(undefined)).toBe("");
    expect(statusRowLines({})).toEqual([]);
  });

  it("adds the context to the text: the percent, or the count when the size is not known", () => {
    expect(contextBadge({ used: 54_200, size: 128_000 })).toBe(" · context 42%");
    expect(contextBadge({ used: 54_200 })).toBe(" · context 54.2k tokens");
  });

  it("has a tooltip line for each number that is there", () => {
    expect(
      statusRowLines({
        usage: { used: 54_200, size: 128_000, cost: { amount: 0.12, currency: "USD" } },
        turn: { lastMs: 8432 },
        cwd: "D:\\work\\pi-python",
      }),
    ).toEqual(["Context: 42% (54.2k of 128k tokens)", "Cost: $0.12", "Last turn: 8.4 s", "Folder: D:\\work\\pi-python"]);
    expect(statusRowLines({ usage: { used: 500 } })).toEqual(["Context: 500 tokens"]);
  });

  it("has no last-turn line while a turn runs", () => {
    expect(statusRowLines({ turn: { elapsedMs: 5000 }, cwd: "/p" })).toEqual(["Folder: /p"]);
  });
});
