import { describe, expect, it } from "vitest";

import {
  DEFAULT_MENTION_EXCLUDES,
  buildFindFilesExcludeGlob,
  createGitignorePathFilter,
  createPathFilter,
  expandExcludePattern,
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

  it("supports [abc] and [!0-9] character classes without matching '/' or failing on unclosed '['", () => {
    const isExcluded = createPathFilter(["**/*.py[cod]", "file[!0-9].txt", "a[b"]);

    expect(isExcluded("mod/a.pyc")).toBe(true);
    expect(isExcluded("mod/a.pyo")).toBe(true);
    expect(isExcluded("mod/a.pyd")).toBe(true);
    expect(isExcluded("mod/a.py")).toBe(false);
    expect(isExcluded("filea.txt")).toBe(true);
    expect(isExcluded("file3.txt")).toBe(false);
    expect(isExcluded("file/.txt")).toBe(false);
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

  it("filters 5,000 paths across a large .gitignore quickly without stalling", () => {
    const lines: string[] = [];
    for (let i = 0; i < 3000; i++) {
      lines.push(`gen_${i}/`);
    }
    const parsed = parseGitignorePatterns(lines.join("\n"));
    const isExcluded = createGitignorePathFilter(DEFAULT_MENTION_EXCLUDES, parsed);
    const paths = Array.from({ length: 5000 }, (_, i) => (i % 10 === 0 ? `gen_${i}/out.ts` : `src/mod_${i % 50}/f_${i}.ts`));

    const start = Date.now();
    let excludedCount = 0;
    for (const p of paths) {
      if (isExcluded(p)) excludedCount++;
    }
    const elapsedMs = Date.now() - start;

    expect(excludedCount).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(1000);
  });
});
