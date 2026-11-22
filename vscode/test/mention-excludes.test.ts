import { describe, expect, it } from "vitest";

import {
  DEFAULT_MENTION_EXCLUDES,
  buildFindFilesExcludeGlob,
  compileGitignoreFilter,
  compileLinearGlob,
  createGitignorePathFilter,
  createPathFilter,
  expandExcludePattern,
  globToRegExp,
  parseGitignorePatterns,
  selectSafeFindFilesGitignoreExcludes,
} from "../src/core/mention-excludes.js";

describe("expandExcludePattern", () => {
  it("keeps simple patterns and expands single-level brace alternations", () => {
    expect(expandExcludePattern("**/node_modules/**")).toEqual(["**/node_modules/**"]);
    expect(expandExcludePattern("**/*.{pyc,pyo}")).toEqual(["**/*.pyc", "**/*.pyo"]);
    expect(expandExcludePattern("{dist,build}/**/*.{js,map}")).toEqual([
      "dist/**/*.js",
      "dist/**/*.map",
      "build/**/*.js",
      "build/**/*.map",
    ]);
  });

  it("rejects empty, nested, unbalanced, or raw comma patterns", () => {
    expect(expandExcludePattern("   ")).toEqual([]);
    expect(expandExcludePattern("{a,{b,c}}")).toEqual([]);
    expect(expandExcludePattern("**/*.{pyc")).toEqual([]);
    expect(expandExcludePattern("foo,bar")).toEqual([]);
  });
});

describe("parseGitignorePatterns", () => {
  it("parses comments, directories, anchored paths, and ! negations", () => {
    const parsed = parseGitignorePatterns(`
# comment
*.log
!important.log
/dist
build/
docs/generated
\\#literal.txt
`);
    expect(parsed.excludes).toContain("**/*.log");
    expect(parsed.excludes).toContain("dist");
    expect(parsed.excludes).toContain("dist/**");
    expect(parsed.excludes).toContain("**/build");
    expect(parsed.excludes).toContain("**/build/**");
    expect(parsed.excludes).toContain("docs/generated");
    expect(parsed.excludes).toContain("docs/generated/**");
    expect(parsed.excludes).toContain("**/#literal.txt");
    expect(parsed.negations).toContain("**/important.log");
    expect(parsed.rules.map((r) => r.negated)).toEqual([false, true, false, false, false, false]);
    expect(parsed.truncated).toBeUndefined();
  });

  it("marks truncated: true only when non-comment rules exceed MAX_GITIGNORE_RULES (2000)", () => {
    const withinLimit = Array.from({ length: 2000 }, (_, i) => `rule_${i}/`).concat(["# trailing comment", ""]).join("\n");
    const parsedWithin = parseGitignorePatterns(withinLimit);
    expect(parsedWithin.rules).toHaveLength(2000);
    expect(parsedWithin.truncated).toBeUndefined();

    const overLimit = Array.from({ length: 2005 }, (_, i) => `rule_${i}/`).join("\n");
    const parsedOver = parseGitignorePatterns(overLimit);
    expect(parsedOver.rules).toHaveLength(2000);
    expect(parsedOver.truncated).toBe(true);
  });
});

describe("buildFindFilesExcludeGlob, glob classes, and gitignore filters", () => {
  it("combines valid patterns into a VS Code findFiles exclude union", () => {
    expect(buildFindFilesExcludeGlob(["**/*.{pyc,pyo}", "**/dist/**", "{bad"])).toBe(
      "{**/*.pyc,**/*.pyo,**/dist/**}",
    );
    expect(buildFindFilesExcludeGlob(["**/dist/**"])).toBe("**/dist/**");
    expect(buildFindFilesExcludeGlob([])).toBe("");
  });

  it("keeps DEFAULT_MENTION_EXCLUDES tool-specific without hiding dist/build source dirs by default", () => {
    const isDefaultExcluded = createPathFilter(DEFAULT_MENTION_EXCLUDES);

    expect(isDefaultExcluded(".git/index.lock")).toBe(true);
    expect(isDefaultExcluded("sub/.git/HEAD")).toBe(true);
    expect(isDefaultExcluded("node_modules/pkg/index.js")).toBe(true);
    expect(isDefaultExcluded("tui/target/debug/zypi")).toBe(true);
    expect(isDefaultExcluded("tools/build/x.ts")).toBe(false);
    expect(isDefaultExcluded("dist/webview.js")).toBe(false);
    expect(isDefaultExcluded(".github/workflows/ci.yml")).toBe(false);
  });

  it("supports [abc], [!0-9], and [!] character classes with $& without matching '/' or failing on unclosed '['", () => {
    const isExcluded = createPathFilter(["**/*.py[cod]", "file[!0-9].txt", "neg[!$&].txt", "pos[$&].txt", "a[b"]);

    expect(isExcluded("mod/a.pyc")).toBe(true);
    expect(isExcluded("mod/a.pyo")).toBe(true);
    expect(isExcluded("mod/a.pyd")).toBe(true);
    expect(isExcluded("mod/a.py")).toBe(false);
    expect(isExcluded("filea.txt")).toBe(true);
    expect(isExcluded("file3.txt")).toBe(false);
    expect(isExcluded("file/.txt")).toBe(false);
    expect(isExcluded("nega.txt")).toBe(true);
    expect(isExcluded("neg$.txt")).toBe(false);
    expect(isExcluded("neg&.txt")).toBe(false);
    expect(isExcluded("pos$.txt")).toBe(true);
    expect(isExcluded("pos&.txt")).toBe(true);
    expect(isExcluded("posa.txt")).toBe(false);
    expect(isExcluded("a[b")).toBe(true);
  });

  it("applies ordered gitignore rules (last match wins) while keeping base excludes active", () => {
    const parsed = parseGitignorePatterns(`
dist/
*.log
!important.log
debug/important.log
/tmp-out/
`);
    const isExcluded = createGitignorePathFilter(DEFAULT_MENTION_EXCLUDES, parsed);

    expect(isExcluded("dist/webview.js")).toBe(true);
    expect(isExcluded("a.log")).toBe(true);
    expect(isExcluded("important.log")).toBe(false);
    expect(isExcluded("debug/important.log")).toBe(true);
    expect(isExcluded("tmp-out/cache.json")).toBe(true);
    expect(isExcluded("node_modules/important.log")).toBe(true);
    expect(isExcluded("src/app.ts")).toBe(false);
  });

  it("selects only safe findFiles gitignore excludes before negations", () => {
    const anchoredNeg = parseGitignorePatterns(`
dist/
.vscode/
!.vscode/extensions.json
build/
`);
    const safeAnchored = selectSafeFindFilesGitignoreExcludes(anchoredNeg);
    expect(safeAnchored).toContain("**/dist");
    expect(safeAnchored).toContain("**/build");
    expect(safeAnchored).not.toContain("**/.vscode");

    const wildcardNeg = parseGitignorePatterns(`
*.log
!important.log
cache/
`);
    const safeWildcard = selectSafeFindFilesGitignoreExcludes(wildcardNeg);
    expect(safeWildcard).not.toContain("**/*.log");
    expect(safeWildcard).toContain("**/cache");
  });

  it("matches pathological multi-wildcard globs in bounded time while collapsing repeated segment stars", () => {
    const evil = "**/a*a*a*a*a*a*a*a*a*a*b";
    const isExcluded = createPathFilter([evil, "**/foo***bar.log", "**/a*b*c*d/**"]);
    const input = `${"a".repeat(40)}/${"a".repeat(40)}`;

    const start = Date.now();
    expect(isExcluded(input)).toBe(false);
    expect(Date.now() - start).toBeLessThan(500);

    // Repeated '*' inside a segment collapses cleanly and stays within one path segment.
    expect(isExcluded("dir/foo123bar.log")).toBe(true);
    expect(isExcluded("dir/foo/bar.log")).toBe(false);
    expect(isExcluded("pkg/a1b2c3d/out.js")).toBe(true);
  });

  it("does not drop a floating pattern with 5 or more stars and matches a 255-character name without a stall", () => {
    const filter = compileGitignoreFilter(DEFAULT_MENTION_EXCLUDES, parseGitignorePatterns("a*a*a*a*a*b\n"));
    expect(filter.dropped).toBe(0);
    const longName = "a".repeat(255);

    const start = performance.now();
    expect(filter.isExcluded(longName)).toBe(false);
    expect(filter.isExcluded(`${longName.slice(0, 250)}b`)).toBe(true);
    expect(filter.isExcluded(`dir/${"a".repeat(251)}b`)).toBe(true);
    // The old regex needed about 9.6 s for the first call; the budget is 1000 times the new cost.
    expect(performance.now() - start).toBeLessThan(1000);
  });

  it("keeps a file visible when a `!` rule has 6 stars (the rule is not dropped)", () => {
    const parsed = parseGitignorePatterns("*\n!a*a*a*a*a*a*b\n");
    const filter = compileGitignoreFilter(DEFAULT_MENTION_EXCLUDES, parsed);
    expect(filter.dropped).toBe(0);
    expect(filter.droppedNegations).toBe(0);
    expect(filter.isExcluded("axaxaxaxaxaxb")).toBe(false);
    expect(filter.isExcluded("axaxaxaxaxb")).toBe(true);
    expect(filter.isExcluded("nope.txt")).toBe(true);
  });

  it("matches a glob with many `**` segments in bounded time", () => {
    const filter = compileGitignoreFilter([], parseGitignorePatterns("**/a/**/a/**/a/**/a/**/a/**/b\n"));
    expect(filter.dropped).toBe(0);
    const start = performance.now();
    expect(filter.isExcluded(Array(150).fill("a").join("/"))).toBe(false);
    expect(filter.isExcluded(`${Array(150).fill("a").join("/")}/b`)).toBe(true);
    expect(performance.now() - start).toBeLessThan(1000);
  });

  it("counts every pattern that cannot be used, and says how many of them are `!` rules", () => {
    const parsed = parseGitignorePatterns("ok.txt\n!a{b{c,d},e}\nx{a,b\n" + `!${"d/".repeat(600)}z\n`);
    expect(parsed.dropped).toBe(2);
    expect(parsed.droppedNegations).toBe(1);

    const filter = compileGitignoreFilter([], parsed);
    expect(filter.dropped).toBeGreaterThanOrEqual(1);
    expect(filter.droppedNegations).toBe(filter.dropped);
    expect(filter.isExcluded("ok.txt")).toBe(true);
  });

  it("limits the linear matchers of the whole filter, not of each run of rules, and counts the rest as dropped", () => {
    const lines: string[] = [];
    for (let i = 0; i < 600; i++) lines.push(`${i % 2 === 1 ? "!" : ""}a${i}*b*c*d*e${i}`);
    const filter = compileGitignoreFilter([], parseGitignorePatterns(`${lines.join("\n")}\n`));
    // Each of the 600 alternating rules is its own run; a limit per run would keep all of them.
    expect(filter.regexCount).toBe(100);
    expect(filter.dropped).toBe(1200 - 100);
    expect(filter.droppedNegations).toBeGreaterThan(0);
    expect(filter.droppedNegations).toBeLessThan(filter.dropped);
    expect(filter.isExcluded("a0xbxcxdxe0")).toBe(true);
    expect(filter.isExcluded("unrelated/file.ts")).toBe(false);
  });

  it("matches the same paths with the linear matcher as with the regex (differential, fixed seed)", () => {
    let seed = 20261122;
    const rand = (n: number) => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed % n;
    };
    const pick = <T,>(items: readonly T[]): T => items[rand(items.length)] as T;
    const patternParts = ["a", "b", "ab", "*", "?", "[ab]", "[!a]", "**", ".", "a*b"];
    const nameChars = ["a", "b", ".", "c"];
    const makeGlob = () => {
      const segments = Array.from({ length: 1 + rand(4) }, () => {
        if (rand(5) === 0) return "**";
        return Array.from({ length: 1 + rand(3) }, () => pick(patternParts).replace("**", "*")).join("");
      });
      return segments.join("/");
    };
    const makePath = () =>
      Array.from({ length: 1 + rand(5) }, () => Array.from({ length: rand(5) }, () => pick(nameChars)).join("") || "a").join("/");

    let compared = 0;
    for (let i = 0; i < 400; i++) {
      const glob = makeGlob();
      const re = globToRegExp(glob);
      const linear = compileLinearGlob(glob);
      expect(re, glob).toBeDefined();
      expect(linear, glob).toBeDefined();
      for (let j = 0; j < 25; j++) {
        const target = makePath();
        expect(linear!.test(target), `${glob} vs ${target}`).toBe(re!.test(target));
        compared++;
      }
    }
    expect(compared).toBe(10_000);
  });

  it("filters 5,000 paths across a large .gitignore with bounded compiled regex count and no stalling", () => {
    const lines: string[] = [];
    for (let i = 0; i < 3000; i++) {
      lines.push(`gen_${i}/`);
    }
    const parsed = parseGitignorePatterns(lines.join("\n"));
    const { isExcluded, regexCount } = compileGitignoreFilter(DEFAULT_MENTION_EXCLUDES, parsed);
    expect(regexCount).toBeGreaterThan(0);
    expect(regexCount).toBeLessThanOrEqual(20);

    const paths = Array.from({ length: 5000 }, (_, i) => (i % 10 === 0 ? `gen_${i}/out.ts` : `src/mod_${i % 50}/f_${i}.ts`));

    const start = Date.now();
    let excludedCount = 0;
    for (const p of paths) {
      if (isExcluded(p)) excludedCount++;
    }
    const elapsedMs = Date.now() - start;

    expect(excludedCount).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(10_000);
  });
});
