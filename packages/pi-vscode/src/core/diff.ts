/**
 * The text work behind the diff view (docs/specs/2026-11-04-vscode-extension-design.md, section 8).
 * The agent sends no old and new text, so the extension rebuilds them: it applies the `edit` tool's
 * replacements to the file it read before the call, and it applies the unified patch of the result
 * backwards to the file as it is after the call. All of it works on text with LF line ends.
 */

export function normalizeLf(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

export interface EditReplacement {
  oldText: string;
  newText: string;
}

/** `rawInput.edits` of the `edit` tool, or `undefined` if it has another shape. */
export function readEdits(rawInput: unknown): EditReplacement[] | undefined {
  if (typeof rawInput !== "object" || rawInput === null) return undefined;
  const edits = (rawInput as { edits?: unknown }).edits;
  if (!Array.isArray(edits) || edits.length === 0) return undefined;
  const out: EditReplacement[] = [];
  for (const edit of edits) {
    if (typeof edit !== "object" || edit === null) return undefined;
    const { oldText, newText } = edit as { oldText?: unknown; newText?: unknown };
    if (typeof oldText !== "string" || typeof newText !== "string") return undefined;
    out.push({ oldText, newText });
  }
  return out;
}

/**
 * What the `edit` tool does when every `oldText` matches exactly once in the original and no two
 * matches overlap. Returns `undefined` otherwise (the tool also has a fuzzy match, which this does
 * not copy: such a call gets no proposed diff).
 */
export function applyEdits(original: string, edits: EditReplacement[]): string | undefined {
  const text = normalizeLf(original);
  const spans: { start: number; end: number; newText: string }[] = [];
  for (const edit of edits) {
    const oldText = normalizeLf(edit.oldText);
    if (oldText === "") return undefined;
    const first = text.indexOf(oldText);
    if (first < 0 || text.indexOf(oldText, first + 1) >= 0) return undefined;
    spans.push({ start: first, end: first + oldText.length, newText: normalizeLf(edit.newText) });
  }
  spans.sort((a, b) => a.start - b.start);
  for (let i = 1; i < spans.length; i++) {
    if ((spans[i]?.start ?? 0) < (spans[i - 1]?.end ?? 0)) return undefined;
  }
  let result = text;
  for (const span of [...spans].reverse()) {
    result = result.slice(0, span.start) + span.newText + result.slice(span.end);
  }
  return result;
}

interface Hunk {
  newStart: number;
  oldLines: string[];
  newLines: string[];
}

const HUNK_HEADER = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

export function parsePatch(patch: string): Hunk[] {
  const hunks: Hunk[] = [];
  let current: Hunk | undefined;
  const lines = normalizeLf(patch).split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  for (const line of lines) {
    const header = HUNK_HEADER.exec(line);
    if (header) {
      current = { newStart: Number(header[1]), oldLines: [], newLines: [] };
      hunks.push(current);
      continue;
    }
    if (!current) continue;
    if (line.startsWith("\\")) continue;
    const mark = line[0];
    const body = line.slice(1);
    if (mark === "+") current.newLines.push(body);
    else if (mark === "-") current.oldLines.push(body);
    else if (mark === " ") {
      current.oldLines.push(body);
      current.newLines.push(body);
    } else if (line === "") {
      current.oldLines.push("");
      current.newLines.push("");
    }
  }
  return hunks;
}

function findBlock(lines: string[], block: string[], expected: number): number {
  if (block.length === 0) return Math.min(Math.max(expected, 0), lines.length);
  let best = -1;
  for (let i = 0; i + block.length <= lines.length; i++) {
    let same = true;
    for (let j = 0; j < block.length; j++) {
      if (lines[i + j] !== block[j]) {
        same = false;
        break;
      }
    }
    if (same && (best < 0 || Math.abs(i - expected) < Math.abs(best - expected))) best = i;
  }
  return best;
}

/**
 * The file as it was before `patch` made `after`. A hunk whose new side is not in `after` makes it
 * fail (the file changed since the call). A trailing blank line of a hunk that the patch cut
 * (`n` lines of context) is not a problem: only the changed block and its context are matched.
 */
export function reversePatch(after: string, patch: string): string | undefined {
  const hunks = parsePatch(patch);
  if (hunks.length === 0) return undefined;
  const lines = normalizeLf(after).split("\n");
  let shift = 0;
  for (const hunk of hunks) {
    const expected = Math.max(hunk.newStart - 1, 0) + shift;
    const at = findBlock(lines, hunk.newLines, expected);
    if (at < 0) return undefined;
    lines.splice(at, hunk.newLines.length, ...hunk.oldLines);
    shift += hunk.oldLines.length - hunk.newLines.length;
  }
  return lines.join("\n");
}
