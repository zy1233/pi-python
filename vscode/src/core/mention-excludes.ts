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
  "**/.cache/**",
  "**/.pytest_cache/**",
  "**/.mypy_cache/**",
  "**/.ruff_cache/**",
  "**/.tox/**",
  "**/.turbo/**",
  "**/.svelte-kit/**",
];

export interface GitignoreRule {
  negated: boolean;
  patterns: string[];
}

export interface ParsedGitignore {
  excludes: string[];
  negations: string[];
  rules: GitignoreRule[];
  truncated?: boolean;
  /** Lines that gave no usable pattern (nested braces, too many brace alternatives). `droppedNegations` counts the `!` lines among them. */
  dropped?: number;
  droppedNegations?: number;
}

/** What a compiled glob offers: a `RegExp`, or the linear matcher for a glob that a regex could not match in bounded time. */
export interface GlobMatcher {
  test(target: string): boolean;
}

const MAX_BRACE_EXPANSIONS = 32;
const MAX_GITIGNORE_RULES = 2000;
const MAX_FILTER_PATTERNS = 4000;
const REGEX_CHUNK_SIZE = 250;
const MAX_GLOB_LENGTH = 1024;
/** The total for one filter: each linear matcher costs O(path length x glob length) for every path. */
const MAX_LINEAR_MATCHERS = 100;
/** A regex over one segment with more `*` runs than this can take O(n^k) time (k = the number of runs). */
const MAX_REGEX_SEGMENT_STARS = 2;
/** `**` between two segments multiplies the backtracking; a leading and a trailing `**` do not. */
const MAX_REGEX_MIDDLE_GLOBSTARS = 1;

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
 * Parses root `.gitignore` lines into normalized workspace-relative globs, negations, and ordered rules.
 */
export function parseGitignorePatterns(content: string): ParsedGitignore {
  const excludes: string[] = [];
  const negations: string[] = [];
  const rules: GitignoreRule[] = [];
  const seenExcludes = new Set<string>();
  const seenNegations = new Set<string>();
  let truncated = false;
  let dropped = 0;
  let droppedNegations = 0;

  for (const rawLine of content.split(/\r?\n/)) {
    let line = trimTrailingUnescapedSpaces(rawLine);
    if (line === "" || line.startsWith("#")) continue;

    if (rules.length >= MAX_GITIGNORE_RULES) {
      truncated = true;
      break;
    }

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
    const rulePatterns: string[] = [];
    const seenInRule = new Set<string>();

    for (const candidate of candidates) {
      for (const expanded of expandExcludePattern(candidate)) {
        if (!seenInRule.has(expanded)) {
          seenInRule.add(expanded);
          rulePatterns.push(expanded);
        }
        if (!seen.has(expanded)) {
          seen.add(expanded);
          target.push(expanded);
        }
      }
    }

    if (rulePatterns.length > 0) {
      rules.push({ negated, patterns: rulePatterns });
    } else {
      dropped++;
      if (negated) droppedNegations++;
    }
  }

  const result: ParsedGitignore = { excludes, negations, rules };
  if (truncated) result.truncated = true;
  if (dropped > 0) {
    result.dropped = dropped;
    result.droppedNegations = droppedNegations;
  }
  return result;
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

function parseGlobCharacterClass(
  glob: string,
  startIndex: number,
): { fragment: string; nextIndex: number } | undefined {
  let cursor = startIndex + 1;
  if (cursor >= glob.length) return undefined;

  let negated = false;
  if (glob[cursor] === "!" || glob[cursor] === "^") {
    negated = true;
    cursor++;
  }

  let body = "";
  let closed = false;
  while (cursor < glob.length) {
    const ch = glob[cursor] ?? "";
    if (ch === "/") {
      return undefined;
    }
    if (ch === "]" && body.length > 0) {
      closed = true;
      cursor++;
      break;
    }
    if (ch === "\\" && cursor + 1 < glob.length) {
      const nextCh = glob[cursor + 1] ?? "";
      if (nextCh === "/") return undefined;
      body += nextCh === "\\" || nextCh === "]" || nextCh === "[" ? `\\${nextCh}` : nextCh;
      cursor += 2;
      continue;
    }
    body += ch === "[" || ch === "^" ? `\\${ch}` : ch;
    cursor++;
  }

  if (!closed || body === "") return undefined;
  return {
    fragment: negated ? `(?!/)[^${body}]` : `[${body}]`,
    nextIndex: cursor,
  };
}

function cleanGlob(glob: string): string {
  return glob
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "")
    .replace(/\*{3,}/g, "**");
}

function globToRegExpSource(glob: string): string | undefined {
  const clean = cleanGlob(glob);
  if (clean === "") return undefined;

  let regex = "";
  let i = 0;
  while (i < clean.length) {
    if (clean.startsWith("**/", i) && (i === 0 || clean[i - 1] === "/")) {
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
    if (
      clean.startsWith("**", i) &&
      (i === 0 || clean[i - 1] === "/") &&
      (i + 2 === clean.length || clean[i + 2] === "/")
    ) {
      regex += ".*";
      i += 2;
      continue;
    }
    const ch = clean[i] ?? "";
    if (ch === "[") {
      const charClass = parseGlobCharacterClass(clean, i);
      if (charClass) {
        regex += charClass.fragment;
        i = charClass.nextIndex;
        continue;
      }
      regex += "\\[";
    } else if (ch === "*") {
      while (clean[i] === "*") i++;
      regex += "[^/]*";
      continue;
    } else if (ch === "?") {
      regex += "[^/]";
    } else if (/[.+^$(){}|\\\]/]/.test(ch)) {
      regex += `\\${ch}`;
    } else {
      regex += ch;
    }
    i++;
  }
  try {
    new RegExp(`^(?:${regex})$`);
    return regex;
  } catch {
    return undefined;
  }
}

export function globToRegExp(glob: string): RegExp | undefined {
  const src = globToRegExpSource(glob);
  if (src === undefined) return undefined;
  try {
    return new RegExp(`^${src}$`);
  } catch {
    return undefined;
  }
}

/**
 * True when the regex of this glob cannot take more than about quadratic time. Backtracking is
 * exponential in the number of `*` runs of one segment and in the number of `**` between segments,
 * so a glob over the limits is matched by `compileLinearGlob` instead of being dropped.
 */
function isRegexSafeGlob(clean: string): boolean {
  const segments = clean.split("/");
  let middleGlobstars = 0;
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i] ?? "";
    if (segment === "**") {
      if (i > 0 && i < segments.length - 1) middleGlobstars++;
      continue;
    }
    const runs = segment.match(/\*+/g);
    if ((runs?.length ?? 0) > MAX_REGEX_SEGMENT_STARS) return false;
  }
  return middleGlobstars <= MAX_REGEX_MIDDLE_GLOBSTARS;
}

type SegmentToken =
  | { kind: "literal"; char: string }
  | { kind: "any" }
  | { kind: "star" }
  | { kind: "class"; re: RegExp };

type GlobSegment = { globstar: true; min: number } | { globstar: false; tokens: SegmentToken[] };

function tokenizeSegment(segment: string): SegmentToken[] | undefined {
  const tokens: SegmentToken[] = [];
  let i = 0;
  while (i < segment.length) {
    const ch = segment[i] ?? "";
    if (ch === "[") {
      const charClass = parseGlobCharacterClass(segment, i);
      if (charClass) {
        try {
          tokens.push({ kind: "class", re: new RegExp(`^${charClass.fragment}$`) });
        } catch {
          return undefined;
        }
        i = charClass.nextIndex;
        continue;
      }
      tokens.push({ kind: "literal", char: "[" });
    } else if (ch === "*") {
      while (segment[i] === "*") i++;
      tokens.push({ kind: "star" });
      continue;
    } else if (ch === "?") {
      tokens.push({ kind: "any" });
    } else {
      tokens.push({ kind: "literal", char: ch });
    }
    i++;
  }
  return tokens;
}

function segmentMatches(tokens: readonly SegmentToken[], text: string): boolean {
  let t = 0;
  let s = 0;
  let starToken = -1;
  let starText = 0;
  while (s < text.length) {
    const token = tokens[t];
    if (token?.kind === "star") {
      starToken = t++;
      starText = s;
      continue;
    }
    if (
      token !== undefined &&
      (token.kind === "any" ||
        (token.kind === "literal" && token.char === text[s]) ||
        (token.kind === "class" && token.re.test(text[s] ?? "")))
    ) {
      t++;
      s++;
      continue;
    }
    if (starToken >= 0) {
      t = starToken + 1;
      s = ++starText;
      continue;
    }
    return false;
  }
  while (tokens[t]?.kind === "star") t++;
  return t === tokens.length;
}

/**
 * Matches a glob segment by segment with the two-pointer wildcard algorithm: time is O(path length
 * times pattern length) whatever the number of `*` and `**`. It matches what `globToRegExp` matches.
 */
export function compileLinearGlob(glob: string): GlobMatcher | undefined {
  const clean = cleanGlob(glob);
  if (clean === "") return undefined;
  const parts = clean.split("/");
  const segments: GlobSegment[] = [];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i] ?? "";
    if (part === "**") {
      // A trailing `**` after a segment needs at least one more segment (`a/**` is not `a`).
      segments.push({ globstar: true, min: i === parts.length - 1 && parts.length > 1 ? 1 : 0 });
      continue;
    }
    const tokens = tokenizeSegment(part);
    if (tokens === undefined) return undefined;
    segments.push({ globstar: false, tokens });
  }

  return {
    test(target: string): boolean {
      const names = target.split("/");
      let p = 0;
      let n = 0;
      let starP = -1;
      let starN = 0;
      while (n < names.length) {
        const segment = segments[p];
        if (segment?.globstar) {
          if (segment.min === 1 && p === segments.length - 1) return true;
          starP = p++;
          starN = n;
          continue;
        }
        if (segment !== undefined && segmentMatches(segment.tokens, names[n] ?? "")) {
          p++;
          n++;
          continue;
        }
        if (starP >= 0) {
          p = starP + 1;
          n = ++starN;
          continue;
        }
        return false;
      }
      while (segments[p]?.globstar && (segments[p] as { min: number }).min === 0) p++;
      return p === segments.length;
    },
  };
}

/** A `RegExp` when it is bounded, else the linear matcher. `undefined` for a glob that is empty, too long or invalid. */
export function compileGlobMatcher(glob: string): GlobMatcher | undefined {
  const clean = cleanGlob(glob);
  if (clean === "" || clean.length > MAX_GLOB_LENGTH) return undefined;
  return isRegexSafeGlob(clean) ? globToRegExp(clean) : compileLinearGlob(clean);
}

const UNANCHORED_PREFIX = "(?:.+/)?";

interface CompileStats {
  dropped: number;
}

function compilePatterns(
  patterns: readonly string[],
  remaining?: { left: number },
  stats?: CompileStats,
  linearBudget: { left: number } = { left: MAX_LINEAR_MATCHERS },
): GlobMatcher[] {
  const rawSources: string[] = [];
  const seen = new Set<string>();
  const linear: GlobMatcher[] = [];
  const seenLinear = new Set<string>();
  const drop = () => {
    if (stats) stats.dropped++;
  };
  for (const raw of patterns) {
    for (const expanded of expandExcludePattern(raw)) {
      if (remaining && remaining.left <= 0) {
        drop();
        continue;
      }
      const clean = cleanGlob(expanded);
      if (clean === "" || clean.length > MAX_GLOB_LENGTH) {
        drop();
        continue;
      }
      if (isRegexSafeGlob(clean)) {
        const src = globToRegExpSource(expanded);
        if (src === undefined) {
          drop();
        } else if (!seen.has(src)) {
          seen.add(src);
          rawSources.push(src);
          if (remaining) remaining.left--;
        }
        continue;
      }
      if (seenLinear.has(clean)) continue;
      const matcher = linearBudget.left > 0 ? compileLinearGlob(clean) : undefined;
      if (matcher === undefined) {
        drop();
        continue;
      }
      linearBudget.left--;
      seenLinear.add(clean);
      linear.push(matcher);
      if (remaining) remaining.left--;
    }
  }

  // Collapse pairs of `X` and `X/.*` (emitted for every gitignore rule) into `X(?:/.*)?`.
  const sources: string[] = [];
  const consumed = new Set<string>();
  for (const src of rawSources) {
    if (consumed.has(src)) continue;
    if (src.endsWith("/.*")) {
      const base = src.slice(0, -3);
      if (seen.has(base)) continue;
    }
    const withSubtree = `${src}/.*`;
    if (seen.has(withSubtree)) {
      consumed.add(withSubtree);
      sources.push(`${src}(?:/.*)?`);
    } else {
      sources.push(src);
    }
  }

  const unanchored: string[] = [];
  const anchored: string[] = [];
  for (const src of sources) {
    if (src.startsWith(UNANCHORED_PREFIX)) {
      unanchored.push(src.slice(UNANCHORED_PREFIX.length));
    } else {
      anchored.push(src);
    }
  }

  const out: GlobMatcher[] = [];
  const compileGroup = (items: readonly string[], prefix: string) => {
    for (let i = 0; i < items.length; i += REGEX_CHUNK_SIZE) {
      const chunk = items.slice(i, i + REGEX_CHUNK_SIZE);
      try {
        out.push(new RegExp(`^${prefix}(?:${chunk.join("|")})$`));
      } catch {
        for (const body of chunk) {
          try {
            out.push(new RegExp(`^${prefix}${body}$`));
          } catch {
            // Skip invalid source fragments individually if chunk combination failed.
          }
        }
      }
    }
  };

  compileGroup(unanchored, UNANCHORED_PREFIX);
  compileGroup(anchored, "");
  out.push(...linear);
  return out;
}

function normalizeFilterTarget(relPath: string): string {
  return relPath
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "");
}

function matchesAny(matchers: readonly GlobMatcher[], target: string): boolean {
  for (const re of matchers) {
    if (re.test(target)) return true;
  }
  return false;
}

/**
 * Compiles exclude and negation globs into a fast predicate for workspace-relative POSIX paths.
 */
export function createPathFilter(
  patterns: readonly string[],
  negations: readonly string[] = [],
): (relPath: string) => boolean {
  const budget = { left: MAX_FILTER_PATTERNS };
  const linearBudget = { left: MAX_LINEAR_MATCHERS };
  const excludeMatchers = compilePatterns(patterns, budget, undefined, linearBudget);
  const negationMatchers = compilePatterns(negations, { left: MAX_FILTER_PATTERNS }, undefined, linearBudget);
  return (relPath: string): boolean => {
    const clean = normalizeFilterTarget(relPath);
    if (clean === "") return false;
    if (matchesAny(negationMatchers, clean)) return false;
    return matchesAny(excludeMatchers, clean);
  };
}

export interface CompiledGitignoreFilter {
  isExcluded(relPath: string): boolean;
  /** The number of compiled matchers (`RegExp` objects and linear matchers). */
  regexCount: number;
  /** Patterns that could not be used (too long, invalid, over a budget). `droppedNegations` counts those of `!` rules. */
  dropped: number;
  droppedNegations: number;
}

/**
 * Compiles `baseExcludes` (always excluded) plus ordered `.gitignore` rules (last-match-wins),
 * returning both the path predicate and the number of compiled `RegExp` objects.
 */
export function compileGitignoreFilter(
  baseExcludes: readonly string[],
  gitignore: Pick<ParsedGitignore, "rules">,
): CompiledGitignoreFilter {
  const stats: CompileStats = { dropped: 0 };
  let droppedNegations = 0;
  const linearBudget = { left: MAX_LINEAR_MATCHERS };
  const baseMatchers = compilePatterns(baseExcludes, { left: MAX_FILTER_PATTERNS }, stats, linearBudget);
  const segments: { negated: boolean; matchers: GlobMatcher[] }[] = [];
  const budget = { left: MAX_FILTER_PATTERNS };

  let currentNegated: boolean | undefined;
  let currentPatterns: string[] = [];

  const flushSegment = () => {
    if (currentNegated === undefined || currentPatterns.length === 0) return;
    const before = stats.dropped;
    const matchers = compilePatterns(currentPatterns, budget, stats, linearBudget);
    if (currentNegated) droppedNegations += stats.dropped - before;
    if (matchers.length > 0) {
      segments.push({ negated: currentNegated, matchers });
    }
    currentPatterns = [];
  };

  for (const rule of gitignore.rules) {
    if (budget.left <= 0) {
      stats.dropped += rule.patterns.length;
      if (rule.negated) droppedNegations += rule.patterns.length;
      continue;
    }
    if (currentNegated !== undefined && rule.negated !== currentNegated) {
      flushSegment();
    }
    currentNegated = rule.negated;
    currentPatterns.push(...rule.patterns);
  }
  flushSegment();

  const regexCount = baseMatchers.length + segments.reduce((sum, seg) => sum + seg.matchers.length, 0);

  return {
    regexCount,
    dropped: stats.dropped,
    droppedNegations,
    isExcluded(relPath: string): boolean {
      const clean = normalizeFilterTarget(relPath);
      if (clean === "") return false;
      if (matchesAny(baseMatchers, clean)) return true;
      for (let i = segments.length - 1; i >= 0; i--) {
        const seg = segments[i]!;
        if (matchesAny(seg.matchers, clean)) {
          return !seg.negated;
        }
      }
      return false;
    },
  };
}

/**
 * Compiles `baseExcludes` (always excluded) plus ordered `.gitignore` rules (last-match-wins).
 */
export function createGitignorePathFilter(
  baseExcludes: readonly string[],
  gitignore: Pick<ParsedGitignore, "rules">,
): (relPath: string) => boolean {
  return compileGitignoreFilter(baseExcludes, gitignore).isExcluded;
}

function concreteNegatedBase(pattern: string): string | undefined {
  const base = pattern.endsWith("/**") ? pattern.slice(0, -3) : pattern;
  if (base === "" || base.startsWith("**/") || /[*?[\]{}]/.test(base)) {
    return undefined;
  }
  return base;
}

function collectProbePaths(basePath: string): string[] {
  const parts = basePath.split("/").filter(Boolean);
  const paths: string[] = [];
  let acc = "";
  for (const part of parts) {
    acc = acc === "" ? part : `${acc}/${part}`;
    paths.push(acc);
  }
  if (acc !== "") {
    paths.push(`${acc}/__probe__`);
  }
  return paths;
}

/**
 * Selects `.gitignore` exclude patterns safe to hand directly to `vscode.workspace.findFiles`
 * without hiding files re-included by later `!` negation rules.
 */
export function selectSafeFindFilesGitignoreExcludes(
  gitignore: Pick<ParsedGitignore, "rules">,
  maxPatterns = 128,
): string[] {
  const rules = gitignore.rules;
  const out: string[] = [];
  const seen = new Set<string>();

  for (let i = 0; i < rules.length && out.length < maxPatterns; i++) {
    const rule = rules[i]!;
    if (rule.negated) continue;

    let hasWildcardNegationAfter = false;
    const concreteProbeSet = new Set<string>();

    for (let j = i + 1; j < rules.length; j++) {
      const later = rules[j]!;
      if (!later.negated) continue;
      for (const negPattern of later.patterns) {
        const concrete = concreteNegatedBase(negPattern);
        if (concrete === undefined) {
          hasWildcardNegationAfter = true;
          break;
        }
        for (const p of collectProbePaths(concrete)) {
          concreteProbeSet.add(p);
        }
      }
      if (hasWildcardNegationAfter) break;
    }

    if (hasWildcardNegationAfter) continue;
    const probePaths = [...concreteProbeSet];

    for (const pattern of rule.patterns) {
      if (out.length >= maxPatterns) break;
      if (seen.has(pattern)) continue;
      if (probePaths.length > 0) {
        const re = compileGlobMatcher(pattern);
        if (!re || probePaths.some((p) => re.test(p))) {
          continue;
        }
      }
      seen.add(pattern);
      out.push(pattern);
    }
  }

  return out;
}

export function isExcludedRelativePath(
  relPath: string,
  patterns: readonly string[],
  negations: readonly string[] = [],
): boolean {
  return createPathFilter(patterns, negations)(relPath);
}
