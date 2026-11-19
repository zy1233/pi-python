export const DEFAULT_MENTION_EXCLUDES: readonly string[] = [
  "**/.git/**",
  "**/node_modules/**",
  "**/.venv/**",
  "**/venv/**",
  "**/__pycache__/**",
  "**/target/**",
  "**/.vsix-cache/**",
  "**/.next/**",
  "**/.nuxt/**",
  "**/dist/**",
  "**/build/**",
  "**/out/**",
  "**/coverage/**",
  "**/.cache/**",
  "**/.pytest_cache/**",
  "**/.mypy_cache/**",
  "**/.ruff_cache/**",
  "**/.tox/**",
  "**/.turbo/**",
  "**/.svelte-kit/**",
];

const MAX_BRACE_EXPANSIONS = 32;

/**
 * Expands a single-level brace alternation (e.g. `**\/*.{pyc,pyo}`) into plain glob patterns.
 * Nested, unbalanced, or overly broad patterns return `[]` so `findFiles` exclude unions stay valid.
 */
export function expandExcludePattern(pattern: string): string[] {
  const trimmed = pattern.trim().replace(/\\/g, "/");
  if (trimmed === "") return [];

  let inBrace = false;
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i];
    if (ch === "{") {
      if (inBrace) return [];
      inBrace = true;
    } else if (ch === "}") {
      if (!inBrace) return [];
      inBrace = false;
    } else if (ch === "," && !inBrace) {
      return [];
    }
  }
  if (inBrace) return [];

  let queue = [trimmed];
  while (queue.some((item) => item.includes("{"))) {
    const next: string[] = [];
    for (const item of queue) {
      const open = item.indexOf("{");
      if (open < 0) {
        next.push(item);
        continue;
      }
      const close = item.indexOf("}", open);
      if (close < 0) return [];
      const prefix = item.slice(0, open);
      const inner = item.slice(open + 1, close);
      const suffix = item.slice(close + 1);
      for (const alt of inner.split(",")) {
        const cleaned = alt.trim();
        if (cleaned !== "") next.push(`${prefix}${cleaned}${suffix}`);
        if (next.length > MAX_BRACE_EXPANSIONS) return [];
      }
    }
    queue = next;
  }

  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of queue) {
    if (item === "" || item.includes("{") || item.includes("}") || item.includes(",") || seen.has(item)) {
      continue;
    }
    seen.add(item);
    out.push(item);
  }
  return out;
}

function trimTrailingUnescapedSpaces(line: string): string {
  let end = line.length;
  while (end > 0 && line[end - 1] === " ") {
    let backslashes = 0;
    for (let i = end - 2; i >= 0 && line[i] === "\\"; i--) backslashes++;
    if (backslashes % 2 === 1) break;
    end--;
  }
  return line.slice(0, end).replace(/\\ /g, " ");
}

/**
 * Parses root `.gitignore` lines into normalized workspace-relative glob patterns and `!` negations.
 */
export function parseGitignorePatterns(content: string): { excludes: string[]; negations: string[] } {
  const excludes: string[] = [];
  const negations: string[] = [];
  const seenExcludes = new Set<string>();
  const seenNegations = new Set<string>();

  for (const rawLine of content.split(/\r?\n/)) {
    let line = trimTrailingUnescapedSpaces(rawLine);
    if (line === "" || line.startsWith("#")) continue;

    let negated = false;
    if (line.startsWith("!")) {
      negated = true;
      line = line.slice(1);
    } else if (line.startsWith("\\#") || line.startsWith("\\!")) {
      line = line.slice(1);
    }

    line = line.replace(/\\/g, "/");
    if (line === "") continue;

    line = line.replace(/\/+$/, "");
    if (line === "") continue;

    const leadingSlash = line.startsWith("/");
    if (leadingSlash) line = line.slice(1).replace(/^\/+/, "");
    if (line === "") continue;

    const anchored = leadingSlash || (line.includes("/") && !line.startsWith("**/"));
    const base = anchored || line.startsWith("**/") ? line : `**/${line}`;
    const candidates = [base, `${base}/**`];

    const target = negated ? negations : excludes;
    const seen = negated ? seenNegations : seenExcludes;
    for (const candidate of candidates) {
      for (const expanded of expandExcludePattern(candidate)) {
        if (!seen.has(expanded)) {
          seen.add(expanded);
          target.push(expanded);
        }
      }
    }
  }

  return { excludes, negations };
}

/** Builds a single glob string for `vscode.workspace.findFiles` `exclude`. */
export function buildFindFilesExcludeGlob(patterns: Iterable<string>): string {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of patterns) {
    for (const pattern of expandExcludePattern(raw)) {
      if (!seen.has(pattern)) {
        seen.add(pattern);
        out.push(pattern);
      }
    }
  }
  if (out.length === 0) return "";
  return out.length === 1 ? (out[0] ?? "") : `{${out.join(",")}}`;
}

function globToRegExp(glob: string): RegExp | undefined {
  const clean = glob.replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\/+/, "");
  if (clean === "") return undefined;

  let regex = "^";
  let i = 0;
  while (i < clean.length) {
    if (clean.startsWith("**/", i)) {
      regex += "(?:.+/)?";
      i += 3;
      continue;
    }
    if (clean.startsWith("/**/", i)) {
      regex += "(?:/.+/|/)";
      i += 4;
      continue;
    }
    if (clean.startsWith("/**", i) && i + 3 === clean.length) {
      regex += "/.*";
      i += 3;
      continue;
    }
    if (clean.startsWith("**", i)) {
      regex += ".*";
      i += 2;
      continue;
    }
    const ch = clean[i] ?? "";
    if (ch === "*") {
      regex += "[^/]*";
    } else if (ch === "?") {
      regex += "[^/]";
    } else if (/[.+^$()[\]{}|\\]/.test(ch)) {
      regex += `\\${ch}`;
    } else {
      regex += ch;
    }
    i++;
  }
  regex += "$";
  try {
    return new RegExp(regex);
  } catch {
    return undefined;
  }
}

function compilePatterns(patterns: readonly string[]): RegExp[] {
  const out: RegExp[] = [];
  for (const raw of patterns) {
    for (const expanded of expandExcludePattern(raw)) {
      const compiled = globToRegExp(expanded);
      if (compiled) out.push(compiled);
    }
  }
  return out;
}

/**
 * Compiles exclude and negation globs into a fast predicate for workspace-relative POSIX paths.
 */
export function createPathFilter(
  patterns: readonly string[],
  negations: readonly string[] = [],
): (relPath: string) => boolean {
  const excludeMatchers = compilePatterns(patterns);
  const negationMatchers = compilePatterns(negations);
  return (relPath: string): boolean => {
    const clean = relPath
      .replace(/\\/g, "/")
      .replace(/^\.\//, "")
      .replace(/^\/+/, "")
      .replace(/\/+$/, "");
    if (clean === "") return false;
    if (negationMatchers.some((re) => re.test(clean))) return false;
    return excludeMatchers.some((re) => re.test(clean));
  };
}

export function isExcludedRelativePath(
  relPath: string,
  patterns: readonly string[],
  negations: readonly string[] = [],
): boolean {
  return createPathFilter(patterns, negations)(relPath);
}
