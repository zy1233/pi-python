export const MAX_MENTIONS = 20;
export const MAX_MENTION_PATH_CHARS = 512;
export const MAX_MENTION_QUERY_CHARS = 256;
export const DEFAULT_MENTION_LIMIT = 30;

export type MentionKind = "file" | "folder" | "selection";
export type MentionBadge = "selection" | "active" | "open" | "folder" | "secret";

export interface MentionRange {
  startLine: number;
  endLine: number;
}

export interface MentionAttachment {
  kind: MentionKind;
  path: string;
  range?: MentionRange;
}

export interface MentionCandidate {
  kind: MentionKind;
  path: string;
  label: string;
  detail: string;
  badge?: MentionBadge;
  range?: MentionRange;
}

export type RankedMention = MentionCandidate & {
  labelMatches?: number[];
  detailMatches?: number[];
};

function hasControlChar(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

/**
 * Normalizes a workspace-relative path for `@` mentions. Rejects empty paths,
 * control characters, absolute paths, Windows drive prefixes, and `.` / `..` segments.
 */
export function normalizeRelativePath(raw: string, kind: MentionKind): string | undefined {
  if (raw.length === 0 || raw.length > MAX_MENTION_PATH_CHARS || hasControlChar(raw)) {
    return undefined;
  }
  let normalized = raw.replace(/\\/g, "/");
  if (normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) {
    return undefined;
  }
  normalized = normalized.replace(/\/+/g, "/");
  while (normalized.startsWith("./")) {
    normalized = normalized.slice(2);
  }
  normalized = normalized.replace(/\/+$/, "");
  if (normalized.trim() === "" || normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) {
    return undefined;
  }
  const segments = normalized.split("/");
  for (const segment of segments) {
    if (segment === "" || segment === "." || segment === "..") {
      return undefined;
    }
  }
  return kind === "folder" ? `${normalized}/` : normalized;
}

export function mentionKey(item: Pick<MentionAttachment, "kind" | "path" | "range">): string {
  const rangePart = item.range ? `${item.range.startLine}-${item.range.endLine}` : "";
  return `${item.kind}:${item.path}:${rangePart}`;
}

function basename(filePath: string): string {
  const trimmed = filePath.replace(/\/+$/, "");
  const slash = trimmed.lastIndexOf("/");
  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed;
}

function dirname(filePath: string): string {
  const trimmed = filePath.replace(/\/+$/, "");
  const slash = trimmed.lastIndexOf("/");
  return slash > 0 ? trimmed.slice(0, slash) : "";
}

export function mentionLabel(item: Pick<MentionAttachment, "kind" | "path" | "range">): string {
  if (item.kind === "folder") return item.path;
  const base = basename(item.path);
  if (item.kind === "selection" && item.range) {
    const linePart =
      item.range.startLine === item.range.endLine
        ? `L${item.range.startLine}`
        : `L${item.range.startLine}-L${item.range.endLine}`;
    return `${base}:${linePart}`;
  }
  return base;
}

export function mentionDetail(item: Pick<MentionAttachment, "kind" | "path" | "range">): string {
  if (item.kind === "folder") return "Folder";
  if (item.kind === "selection") return item.path;
  return dirname(item.path);
}

export function formatMentionToken(item: Pick<MentionAttachment, "kind" | "path" | "range">): string {
  const ref =
    item.kind === "selection" && item.range
      ? item.range.startLine === item.range.endLine
        ? `${item.path}:L${item.range.startLine}`
        : `${item.path}:L${item.range.startLine}-L${item.range.endLine}`
      : item.path;
  return /\s/.test(ref) ? `@"${ref}"` : `@${ref}`;
}

export function deriveFolders(filePaths: readonly string[]): string[] {
  const folders = new Set<string>();
  for (const raw of filePaths) {
    const clean = raw.replace(/\\/g, "/").replace(/\/+$/, "");
    let idx = clean.indexOf("/");
    while (idx > 0) {
      folders.add(clean.slice(0, idx + 1));
      idx = clean.indexOf("/", idx + 1);
    }
  }
  return [...folders].sort((a, b) => a.localeCompare(b));
}

function isWordBoundary(text: string, index: number): boolean {
  if (index <= 0) return true;
  const prev = text[index - 1] ?? "";
  const curr = text[index] ?? "";
  if (prev === "/" || prev === "\\" || prev === "-" || prev === "_" || prev === "." || prev === " ") {
    return true;
  }
  return prev >= "a" && prev <= "z" && curr >= "A" && curr <= "Z";
}

function isSubsequence(query: string, target: string): boolean {
  let qi = 0;
  for (let ti = 0; ti < target.length && qi < query.length; ti++) {
    if (target[ti] === query[qi]) qi++;
  }
  return qi === query.length;
}

export function fuzzyScore(query: string, text: string): { score: number; indices: number[] } | undefined {
  if (query === "") return { score: 0, indices: [] };
  if (query.length > text.length) return undefined;

  const caseSensitive = query !== query.toLowerCase();
  const q = caseSensitive ? query : query.toLowerCase();
  const t = caseSensitive ? text : text.toLowerCase();

  // Direct substring match: prefer boundary-aligned occurrences and exact matches.
  let searchFrom = 0;
  let bestSubIdx = -1;
  let bestSubScore = -Infinity;
  while (searchFrom <= t.length - q.length) {
    const found = t.indexOf(q, searchFrom);
    if (found < 0) break;
    const exactBonus = t === q ? 80 : 0;
    const boundaryBonus = found === 0 ? 40 : isWordBoundary(text, found) ? 25 : 0;
    const caseBonus = text.slice(found, found + query.length) === query ? 8 : 0;
    const score = 120 + q.length * 12 + exactBonus + boundaryBonus + caseBonus - found * 0.2 - text.length * 0.05;
    if (score > bestSubScore) {
      bestSubScore = score;
      bestSubIdx = found;
    }
    searchFrom = found + 1;
  }
  if (bestSubIdx >= 0) {
    const indices: number[] = [];
    for (let k = 0; k < q.length; k++) indices.push(bestSubIdx + k);
    return { score: bestSubScore, indices };
  }

  if (!isSubsequence(q, t)) return undefined;

  // Bounded DP for optimal boundary & consecutive-run alignment on typical paths/names.
  const qLen = q.length;
  const tLen = t.length;
  if (qLen <= 64 && tLen <= 256) {
    const NEG = -1e9;
    let prevScores = new Float64Array(tLen).fill(NEG);
    const parents = new Int16Array(qLen * tLen).fill(-1);

    for (let ti = 0; ti < tLen; ti++) {
      if (t[ti] !== q[0]) continue;
      const boundary = ti === 0 ? 20 : isWordBoundary(text, ti) ? 15 : 0;
      const exactCase = text[ti] === query[0] ? 2 : 0;
      prevScores[ti] = 10 + boundary + exactCase - ti * 0.05;
    }

    for (let qi = 1; qi < qLen; qi++) {
      const nextScores = new Float64Array(tLen).fill(NEG);
      let bestPrior = NEG;
      let bestPriorIdx = -1;

      for (let ti = 0; ti < tLen; ti++) {
        const priorIdx = ti - 1;
        if (priorIdx >= 0) {
          const candPrior = prevScores[priorIdx] ?? NEG;
          if (candPrior > NEG / 2) {
            const penalized = candPrior;
            if (penalized > bestPrior) {
              bestPrior = penalized;
              bestPriorIdx = priorIdx;
            }
          }
        }
        if (t[ti] !== q[qi]) continue;

        const boundary = isWordBoundary(text, ti) ? 15 : 0;
        const exactCase = text[ti] === query[qi] ? 2 : 0;
        const charScore = 10 + boundary + exactCase;

        let best = NEG;
        let parent = -1;

        const consecPrev = ti > 0 ? (prevScores[ti - 1] ?? NEG) : NEG;
        if (consecPrev > NEG / 2) {
          best = consecPrev + charScore + 15;
          parent = ti - 1;
        }
        if (bestPriorIdx >= 0) {
          const gap = ti - bestPriorIdx - 1;
          const gapScore = bestPrior + charScore - Math.min(gap * 0.5, 10);
          if (gapScore > best) {
            best = gapScore;
            parent = bestPriorIdx;
          }
        }
        nextScores[ti] = best;
        parents[qi * tLen + ti] = parent;
      }
      prevScores = nextScores;
    }

    let bestEnd = -1;
    let bestScore = NEG;
    for (let ti = 0; ti < tLen; ti++) {
      const s = prevScores[ti] ?? NEG;
      if (s > bestScore) {
        bestScore = s;
        bestEnd = ti;
      }
    }
    if (bestEnd >= 0 && bestScore > NEG / 2) {
      const indices = new Array<number>(qLen);
      let cur = bestEnd;
      for (let qi = qLen - 1; qi >= 0; qi--) {
        indices[qi] = cur;
        cur = parents[qi * tLen + cur] ?? -1;
      }
      return { score: bestScore - text.length * 0.05, indices };
    }
  }

  // Fast greedy fallback for unusually long queries/paths.
  const indices: number[] = [];
  let qi = 0;
  let score = 0;
  let lastIdx = -1;
  for (let ti = 0; ti < tLen && qi < qLen; ti++) {
    if (t[ti] !== q[qi]) continue;
    indices.push(ti);
    score += 10 + (ti === 0 ? 20 : isWordBoundary(text, ti) ? 15 : 0) + (lastIdx === ti - 1 ? 15 : 0);
    lastIdx = ti;
    qi++;
  }
  return qi === qLen ? { score: score - text.length * 0.05, indices } : undefined;
}

function candidatePriority(candidate: MentionCandidate, dirMode: boolean): number {
  if (candidate.kind === "selection" || candidate.badge === "selection") return 400;
  if (candidate.badge === "active") return 300;
  if (candidate.badge === "open") return 200;
  if (candidate.kind === "folder" || candidate.badge === "folder") return dirMode ? 200 : 50;
  return 0;
}

function pathDepth(relPath: string): number {
  const clean = relPath.replace(/\/+$/, "");
  if (clean === "") return 0;
  return clean.split("/").length;
}

function splitPathMatches(
  candidate: MentionCandidate,
  pathIndices: number[],
): { labelMatches?: number[]; detailMatches?: number[] } {
  if (candidate.kind === "folder") {
    return { labelMatches: pathIndices };
  }
  if (candidate.kind === "selection") {
    const baseStart = candidate.detail.lastIndexOf("/") + 1;
    const labelMatches = pathIndices.filter((i) => i >= baseStart).map((i) => i - baseStart);
    return {
      detailMatches: pathIndices,
      ...(labelMatches.length > 0 ? { labelMatches } : {}),
    };
  }
  const detailLen = candidate.detail.length;
  const labelOffset = detailLen === 0 ? 0 : detailLen + 1;
  const detailMatches: number[] = [];
  const labelMatches: number[] = [];
  for (const idx of pathIndices) {
    if (detailLen > 0 && idx < detailLen) detailMatches.push(idx);
    else if (idx >= labelOffset) labelMatches.push(idx - labelOffset);
  }
  return {
    ...(labelMatches.length > 0 ? { labelMatches } : {}),
    ...(detailMatches.length > 0 ? { detailMatches } : {}),
  };
}

export function rankMentions(
  candidates: readonly MentionCandidate[],
  query: string,
  limit = DEFAULT_MENTION_LIMIT,
): RankedMention[] {
  if (limit <= 0) return [];
  const trimmed = query.trim();
  const dirMode = trimmed.endsWith("/");

  if (trimmed === "") {
    return [...candidates]
      .sort((a, b) => {
        const priDiff = candidatePriority(b, false) - candidatePriority(a, false);
        if (priDiff !== 0) return priDiff;
        const depthDiff = pathDepth(a.path) - pathDepth(b.path);
        if (depthDiff !== 0) return depthDiff;
        return a.path.localeCompare(b.path);
      })
      .slice(0, limit)
      .map((c) => ({ ...c }));
  }

  const caseSensitive = trimmed !== trimmed.toLowerCase();
  const prefixMatch = caseSensitive ? trimmed : trimmed.toLowerCase();

  const scored: { item: RankedMention; score: number; priority: number }[] = [];
  for (const candidate of candidates) {
    const labelHit = fuzzyScore(trimmed, candidate.label);
    const pathHit = fuzzyScore(trimmed, candidate.path);
    if (!labelHit && !pathHit) continue;

    const pri = candidatePriority(candidate, dirMode);
    let score = Math.max(labelHit ? labelHit.score + 120 : -Infinity, pathHit ? pathHit.score : -Infinity);

    if (dirMode) {
      const targetPath = caseSensitive ? candidate.path : candidate.path.toLowerCase();
      if (targetPath.startsWith(prefixMatch)) {
        score += 200;
        const rest = targetPath.slice(prefixMatch.length).replace(/\/$/, "");
        if (rest !== "" && !rest.includes("/")) {
          score += 100;
        }
      }
    }

    let labelMatches: number[] | undefined;
    let detailMatches: number[] | undefined;
    if (pathHit && (!labelHit || trimmed.includes("/"))) {
      ({ labelMatches, detailMatches } = splitPathMatches(candidate, pathHit.indices));
    } else if (labelHit) {
      labelMatches = labelHit.indices;
    }

    const ranked: RankedMention = {
      ...candidate,
      ...(labelMatches && labelMatches.length > 0 ? { labelMatches } : {}),
      ...(detailMatches && detailMatches.length > 0 ? { detailMatches } : {}),
    };
    scored.push({ item: ranked, score, priority: pri });
  }

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.priority !== a.priority) return b.priority - a.priority;
    if (a.item.path.length !== b.item.path.length) return a.item.path.length - b.item.path.length;
    return a.item.path.localeCompare(b.item.path);
  });

  return scored.slice(0, limit).map((entry) => entry.item);
}

const SAFE_EXACT_NAMES = new Set(["ca.pem", "cacert.pem", "root-ca.pem"]);
const SAFE_SECRET_SUFFIXES = [".example", ".sample", ".template", ".dist", ".md", ".pub", ".d.ts"];
const SECRET_EXACT_NAMES = new Set([
  ".env",
  ".envrc",
  ".npmrc",
  ".pypirc",
  ".netrc",
  "credentials",
  "credentials.json",
  ".git-credentials",
  "service-account.json",
  "serviceaccountkey.json",
  "client_secret.json",
  ".htpasswd",
  "secrets.json",
  "secrets.yaml",
  "secrets.yml",
  "secrets.toml",
  ".secrets",
  "secret.txt",
  "kubeconfig",
  ".pgpass",
  ".dockercfg",
  "terraform.tfvars",
  "terraform.tfstate",
  "id_rsa",
  "id_ed25519",
  "id_ecdsa",
  "id_dsa",
]);
const SECRET_EXTENSIONS = [
  ".env",
  ".pem",
  ".key",
  ".p12",
  ".pfx",
  ".jks",
  ".keystore",
  ".ppk",
  ".tfvars",
  ".tfstate",
  ".secrets",
  ".keys",
];
const SECRET_PREFIXES = [".env.", "id_rsa.", "id_ed25519.", "id_ecdsa.", "id_dsa."];

/**
 * Returns whether a workspace-relative path looks like a credential or secret file
 * that should be badged in the `@` mention picker.
 */
export function isLikelySecretPath(relPath: string): boolean {
  const normalizedPath = relPath
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "")
    .toLowerCase();
  const name = basename(normalizedPath);
  if (name === "") return false;
  if (SAFE_EXACT_NAMES.has(name) || SAFE_SECRET_SUFFIXES.some((suffix) => name.endsWith(suffix))) {
    return false;
  }
  if (
    normalizedPath === ".aws/credentials" ||
    normalizedPath.endsWith("/.aws/credentials") ||
    normalizedPath === ".docker/config.json" ||
    normalizedPath.endsWith("/.docker/config.json")
  ) {
    return true;
  }
  if (SECRET_EXACT_NAMES.has(name)) return true;
  if (SECRET_EXTENSIONS.some((ext) => name.endsWith(ext))) return true;
  if (SECRET_PREFIXES.some((prefix) => name.startsWith(prefix))) return true;
  return false;
}

export function formatPromptWithMentions(text: string, mentions: readonly MentionAttachment[]): string {
  if (mentions.length === 0 || text.trimStart().startsWith("/")) return text;
  const seen = new Set<string>();
  const missing: string[] = [];
  for (const item of mentions) {
    const key = mentionKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    const token = formatMentionToken(item);
    if (!text.includes(token)) {
      missing.push(token);
    }
  }
  if (missing.length === 0) return text;
  return `Referenced context: ${missing.join(" ")}\n\n${text}`;
}

function isTokenBoundaryChar(ch: string | undefined): boolean {
  return ch === undefined || !/[A-Za-z0-9_./\\-]/.test(ch);
}

function isMentionTokenAfterBoundary(text: string, afterIdx: number): boolean {
  const after = afterIdx < text.length ? text[afterIdx] : undefined;
  if (isTokenBoundaryChar(after)) return true;
  if (after === "." || after === "-") {
    const nextAfter = afterIdx + 1 < text.length ? text[afterIdx + 1] : undefined;
    return nextAfter === undefined || /\s/.test(nextAfter);
  }
  return false;
}

/** Returns whether `text` still contains the formatted `@` token for `item` at token boundaries. */
export function containsMentionToken(
  text: string,
  item: Pick<MentionAttachment, "kind" | "path" | "range">,
): boolean {
  const token = formatMentionToken(item);
  if (token === "" || text === "") return false;
  let searchFrom = 0;
  while (searchFrom <= text.length - token.length) {
    const idx = text.indexOf(token, searchFrom);
    if (idx < 0) return false;
    const before = idx > 0 ? text[idx - 1] : undefined;
    if ((before === undefined || !/[A-Za-z0-9_]/.test(before)) && isMentionTokenAfterBoundary(text, idx + token.length)) {
      return true;
    }
    searchFrom = idx + 1;
  }
  return false;
}

export function removeMentionToken(text: string, item: MentionAttachment): string {
  const token = formatMentionToken(item);
  if (token === "" || text === "") return text;

  let current = text;
  let searchFrom = 0;
  while (searchFrom <= current.length - token.length) {
    const idx = current.indexOf(token, searchFrom);
    if (idx < 0) break;

    const before = idx > 0 ? current[idx - 1] : undefined;
    const afterIdx = idx + token.length;
    const after = afterIdx < current.length ? current[afterIdx] : undefined;

    if ((before !== undefined && /[A-Za-z0-9_]/.test(before)) || !isMentionTokenAfterBoundary(current, afterIdx)) {
      searchFrom = idx + 1;
      continue;
    }

    if (after === " " || after === "\t") {
      current = current.slice(0, idx) + current.slice(afterIdx + 1);
    } else if (before === " " || before === "\t") {
      current = current.slice(0, idx - 1) + current.slice(afterIdx);
      searchFrom = Math.max(0, idx - 1);
      continue;
    } else {
      current = current.slice(0, idx) + current.slice(afterIdx);
    }
    searchFrom = idx;
  }
  return current;
}
