import path from "node:path";

import { DiffStore } from "./diff-store.js";
import { applyEdits, normalizeLf, readEdits, reversePatch } from "./diff.js";
import type { Host } from "./host.js";

type PreImage = string | null | undefined;

interface Track {
  id: string;
  tool: "write" | "edit";
  file: string;
  display: string;
  rawInput: Record<string, unknown>;
  pre: Promise<PreImage>;
  preValue: PreImage;
  preSettled: boolean;
  /** The pre-image may have been read after the tool began (spec section 8.1). */
  late: boolean;
  seenFirstUpdate: boolean;
  finished: boolean;
}

export interface DiffInfo {
  key: string;
  title: string;
  note?: string;
}

const MAX_TRACKS = 50;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Keeps what the diff view needs for each `write` and `edit` call (docs/specs/2026-11-04-vscode-extension-design.md,
 * section 8): the file as it was before the call, a proposed diff for the permission card, and the
 * result diff after the call. It never writes a file.
 */
export class DiffTracker {
  readonly store = new DiffStore();
  private readonly tracks = new Map<string, Track>();
  private readonly infos = new Map<string, DiffInfo>();

  constructor(
    private readonly host: Pick<Host, "readFile" | "log">,
    private readonly folder: () => string | undefined,
  ) {}

  clear(): void {
    this.tracks.clear();
    this.infos.clear();
  }

  /** The best diff of a call that the store still holds: the result, else the proposal. */
  info(toolCallId: string): DiffInfo | undefined {
    for (const kind of ["result", "proposed"] as const) {
      const info = this.infos.get(`${toolCallId}:${kind}`);
      if (info && this.store.has(info.key)) return info;
    }
    return undefined;
  }

  /** Called for each `tool_call` and `tool_call_update`. The first one of a `write` or `edit` call starts the read. */
  observe(update: { toolCallId?: unknown; kind?: unknown; rawInput?: unknown }): void {
    if (typeof update.toolCallId !== "string") return;
    const known = this.tracks.get(update.toolCallId);
    if (known) {
      if (known.seenFirstUpdate && !known.preSettled) known.late = true;
      known.seenFirstUpdate = true;
      return;
    }
    if (update.kind !== "edit" || !isRecord(update.rawInput)) return;
    const raw = update.rawInput;
    const given = raw.path;
    const folder = this.folder();
    if (typeof given !== "string" || given === "" || given.startsWith("~") || folder === undefined) return;
    const tool = typeof raw.content === "string" ? "write" : readEdits(raw) ? "edit" : undefined;
    if (!tool) return;
    const file = path.resolve(folder, given);
    const track: Track = {
      id: update.toolCallId,
      tool,
      file,
      display: given,
      rawInput: raw,
      pre: Promise.resolve(undefined),
      preValue: undefined,
      preSettled: false,
      late: false,
      seenFirstUpdate: false,
      finished: false,
    };
    track.pre = this.host.readFile(file).then(
      (value) => {
        track.preValue = value;
        track.preSettled = true;
        return value;
      },
      (error: unknown) => {
        this.host.log.warn(`diff: could not read ${file}: ${error instanceof Error ? error.message : String(error)}`);
        track.preSettled = true;
        return undefined;
      },
    );
    this.tracks.set(track.id, track);
    while (this.tracks.size > MAX_TRACKS) {
      const oldest = this.tracks.keys().next().value;
      if (oldest === undefined) break;
      this.tracks.delete(oldest);
    }
  }

  /**
   * The permission handler calls this and waits, so the pre-image is read before the tool can run.
   * Returns whether the card can offer a diff, or the reason why not.
   */
  async propose(toolCallId: string): Promise<{ canDiff: boolean; note?: string } | undefined> {
    const track = this.tracks.get(toolCallId);
    if (!track) return undefined;
    const pre = await track.pre;
    if (pre === undefined) return { canDiff: false, note: "diff not available (the file cannot be read)" };
    const before = pre === null ? "" : normalizeLf(pre);
    let after: string | undefined;
    if (track.tool === "write") {
      after = normalizeLf(String(track.rawInput.content));
    } else {
      const edits = readEdits(track.rawInput);
      if (pre === null) return { canDiff: false, note: "diff not available (the file does not exist)" };
      after = edits ? applyEdits(before, edits) : undefined;
    }
    if (after === undefined) return { canDiff: false, note: "diff not available" };
    this.put(track, "proposed", before, after, `${track.display} (proposed)`);
    return { canDiff: true };
  }

  /** The call ended. Builds the result diff. Returns `true` if the call now has a diff. */
  async finish(toolCallId: string, status: string, rawOutput: unknown): Promise<boolean> {
    const track = this.tracks.get(toolCallId);
    if (!track || track.finished) return false;
    if (status !== "completed" && status !== "failed") return false;
    track.finished = true;
    if (status === "failed") return false;
    if (!track.preSettled) track.late = true;
    const pre = await track.pre;
    const trustedPre = !track.late && pre !== undefined;
    const beforePre = trustedPre ? (pre === null ? "" : normalizeLf(pre)) : undefined;

    if (track.tool === "write") {
      const after = normalizeLf(String(track.rawInput.content));
      if (beforePre === undefined) {
        this.put(track, "result", "", after, `${track.display} (old content not available)`, "old content not available");
      } else {
        this.put(track, "result", beforePre, after, `${track.display} (written)`);
      }
      return true;
    }

    const current = await this.host.readFile(track.file);
    if (typeof current !== "string") return this.keepProposal(track);
    const after = normalizeLf(current);
    const patch = detailsPatch(rawOutput);
    const reversed = patch === undefined ? undefined : reversePatch(after, patch);
    if (reversed !== undefined) {
      this.put(track, "result", reversed, after, `${track.display} (edited)`);
      return true;
    }
    if (beforePre !== undefined) {
      this.put(track, "result", beforePre, after, `${track.display} (edited; the file changed after the call)`, "the file changed after the call");
      return true;
    }
    return this.keepProposal(track);
  }

  private keepProposal(track: Track): boolean {
    return this.infos.has(`${track.id}:proposed`) && this.store.has(`${track.id}:proposed`);
  }

  private put(track: Track, kind: "proposed" | "result", before: string, after: string, title: string, note?: string): void {
    const key = `${track.id}:${kind}`;
    this.store.put(key, before, after);
    const info: DiffInfo = { key, title };
    if (note !== undefined) info.note = note;
    this.infos.set(key, info);
  }
}

function detailsPatch(rawOutput: unknown): string | undefined {
  if (!isRecord(rawOutput) || !isRecord(rawOutput.details)) return undefined;
  const { diff, patch } = rawOutput.details;
  if (typeof diff === "string" && diff !== "") return diff;
  if (typeof patch === "string" && patch !== "") return patch;
  return undefined;
}
