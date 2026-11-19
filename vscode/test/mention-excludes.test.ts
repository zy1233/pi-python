import { describe, expect, it } from "vitest";

import {
  DEFAULT_MENTION_EXCLUDES,
  buildFindFilesExcludeGlob,
  createPathFilter,
  expandExcludePattern,
  parseGitignorePatterns,
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
  });
});

describe("buildFindFilesExcludeGlob and createPathFilter", () => {
  it("combines valid patterns into a VS Code findFiles exclude union", () => {
    expect(buildFindFilesExcludeGlob(["**/*.{pyc,pyo}", "**/dist/**", "{bad"])).toBe(
      "{**/*.pyc,**/*.pyo,**/dist/**}",
    );
    expect(buildFindFilesExcludeGlob(["**/dist/**"])).toBe("**/dist/**");
    expect(buildFindFilesExcludeGlob([])).toBe("");
  });

  it("excludes noisy workspace directories and respects gitignore negations", () => {
    const parsed = parseGitignorePatterns(`
*.log
!keep.log
/tmp-out/
`);
    const isExcluded = createPathFilter([...DEFAULT_MENTION_EXCLUDES, ...parsed.excludes], parsed.negations);

    expect(isExcluded(".git/index.lock")).toBe(true);
    expect(isExcluded("sub/.git/HEAD")).toBe(true);
    expect(isExcluded("node_modules/pkg/index.js")).toBe(true);
    expect(isExcluded("tui/target/debug/zypi")).toBe(true);
    expect(isExcluded("dist/webview.js")).toBe(true);
    expect(isExcluded("debug.log")).toBe(true);
    expect(isExcluded("tmp-out/cache.json")).toBe(true);

    expect(isExcluded("keep.log")).toBe(false);
    expect(isExcluded("src/app.ts")).toBe(false);
    expect(isExcluded(".github/workflows/ci.yml")).toBe(false);
  });
});
