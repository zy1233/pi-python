/** Texts of the diff view, in memory, oldest out first (spec section 8: 50 calls, 4 MiB). */
export type DiffSide = "before" | "after";

export interface DiffStoreLimits {
  maxEntries: number;
  maxBytes: number;
}

export const DEFAULT_DIFF_LIMITS: DiffStoreLimits = { maxEntries: 50, maxBytes: 4 * 1024 * 1024 };

export class DiffStore {
  private readonly entries = new Map<string, { before: string; after: string; bytes: number }>();
  private bytes = 0;

  constructor(private readonly limits: DiffStoreLimits = DEFAULT_DIFF_LIMITS) {}

  put(key: string, before: string, after: string): void {
    this.delete(key);
    const bytes = Buffer.byteLength(before) + Buffer.byteLength(after);
    this.entries.set(key, { before, after, bytes });
    this.bytes += bytes;
    while (this.entries.size > 1 && (this.entries.size > this.limits.maxEntries || this.bytes > this.limits.maxBytes)) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.delete(oldest);
    }
  }

  get(key: string, side: DiffSide): string | undefined {
    return this.entries.get(key)?.[side];
  }

  has(key: string): boolean {
    return this.entries.has(key);
  }

  get size(): number {
    return this.entries.size;
  }

  get totalBytes(): number {
    return this.bytes;
  }

  private delete(key: string): void {
    const entry = this.entries.get(key);
    if (!entry) return;
    this.bytes -= entry.bytes;
    this.entries.delete(key);
  }
}
