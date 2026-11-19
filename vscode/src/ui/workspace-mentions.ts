import path from "node:path";

import * as vscode from "vscode";

import {
  DEFAULT_MENTION_EXCLUDES,
  buildFindFilesExcludeGlob,
  createGitignorePathFilter,
  expandExcludePattern,
  parseGitignorePatterns,
  selectSafeFindFilesGitignoreExcludes,
  type ParsedGitignore,
} from "../core/mention-excludes.js";
import {
  DEFAULT_MENTION_LIMIT,
  deriveFolders,
  mentionDetail,
  mentionLabel,
  normalizeRelativePath,
  rankMentions,
  type MentionBadge,
  type MentionCandidate,
  type RankedMention,
} from "../core/mentions.js";

const CACHE_TTL_MS = 300_000;
const MAX_INDEX_FILES = 5000;
const MAX_GITIGNORE_BYTES = 128 * 1024;

interface CachedIndex {
  folder: string;
  files: string[];
  folders: string[];
  expiresAt: number;
}

function underFolder(file: string, folder: string): boolean {
  const relative = path.relative(folder, file);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function relativePosix(file: string, folder: string): string | undefined {
  if (!underFolder(file, folder)) return undefined;
  return path.relative(folder, file).replace(/\\/g, "/").replace(/\/+$/, "");
}

function collectConfigExcludes(folderUri: vscode.Uri, target: Set<string>): void {
  for (const section of ["files", "search"] as const) {
    const raw = vscode.workspace.getConfiguration(section, folderUri).get<Record<string, unknown>>("exclude");
    if (!raw || typeof raw !== "object") continue;
    for (const [pattern, enabled] of Object.entries(raw)) {
      if (enabled !== true) continue;
      for (const expanded of expandExcludePattern(pattern)) {
        target.add(expanded);
      }
    }
  }
}

async function readRootGitignore(folderUri: vscode.Uri): Promise<ParsedGitignore> {
  try {
    const bytes = await vscode.workspace.fs.readFile(vscode.Uri.joinPath(folderUri, ".gitignore"));
    if (bytes.byteLength > MAX_GITIGNORE_BYTES) return { excludes: [], negations: [], rules: [] };
    return parseGitignorePatterns(new TextDecoder("utf-8").decode(bytes));
  } catch {
    return { excludes: [], negations: [], rules: [] };
  }
}

/**
 * Indexes workspace files/folders and collects active editor context for `@` mentions.
 */
export class WorkspaceMentions implements vscode.Disposable {
  private cache: CachedIndex | undefined;
  private cacheVersion = 0;
  private pathFilter: ((relPath: string) => boolean) | undefined;
  private watcherFolder: string | undefined;
  private watcherDisposables: vscode.Disposable[] = [];
  private pendingIndex: { folder: string; version: number; promise: Promise<CachedIndex> } | undefined;

  async search(folder: string, query: string, limit = DEFAULT_MENTION_LIMIT): Promise<RankedMention[]> {
    if (!vscode.workspace.isTrusted) return [];

    const index = await this.ensureIndex(folder);
    const candidates: MentionCandidate[] = [];
    const seenFiles = new Map<string, MentionBadge | undefined>();

    const editor = vscode.window.activeTextEditor;
    if (editor && editor.document.uri.scheme === "file" && underFolder(editor.document.uri.fsPath, folder)) {
      const rel = normalizeRelativePath(path.relative(folder, editor.document.uri.fsPath), "file");
      if (rel) {
        seenFiles.set(rel, "active");
        if (!editor.selection.isEmpty) {
          const startLine = editor.selection.start.line + 1;
          const endPos = editor.selection.end;
          const endLine =
            endPos.character === 0 && endPos.line > editor.selection.start.line ? endPos.line : endPos.line + 1;
          const range = { startLine, endLine: Math.max(startLine, endLine) };
          candidates.push({
            kind: "selection",
            path: rel,
            range,
            label: mentionLabel({ kind: "selection", path: rel, range }),
            detail: mentionDetail({ kind: "selection", path: rel, range }),
            badge: "selection",
          });
        }
      }
    }

    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        if (!(tab.input instanceof vscode.TabInputText)) continue;
        const uri = tab.input.uri;
        if (uri.scheme !== "file" || !underFolder(uri.fsPath, folder)) continue;
        const rel = normalizeRelativePath(path.relative(folder, uri.fsPath), "file");
        if (rel && !seenFiles.has(rel)) {
          seenFiles.set(rel, "open");
        }
      }
    }

    for (const [filePath, badge] of seenFiles) {
      candidates.push({
        kind: "file",
        path: filePath,
        label: mentionLabel({ kind: "file", path: filePath }),
        detail: mentionDetail({ kind: "file", path: filePath }),
        ...(badge ? { badge } : {}),
      });
    }

    for (const folderPath of index.folders) {
      candidates.push({
        kind: "folder",
        path: folderPath,
        label: mentionLabel({ kind: "folder", path: folderPath }),
        detail: mentionDetail({ kind: "folder", path: folderPath }),
        badge: "folder",
      });
    }

    for (const filePath of index.files) {
      if (seenFiles.has(filePath)) continue;
      candidates.push({
        kind: "file",
        path: filePath,
        label: mentionLabel({ kind: "file", path: filePath }),
        detail: mentionDetail({ kind: "file", path: filePath }),
      });
    }

    return rankMentions(candidates, query, limit);
  }

  dispose(): void {
    this.disposeWatcher();
    this.invalidateCache(true);
    this.pendingIndex = undefined;
  }

  private invalidateCache(clearFilter = false): void {
    this.cacheVersion++;
    this.cache = undefined;
    if (clearFilter) {
      this.pathFilter = undefined;
    }
  }

  private disposeWatcher(): void {
    for (const d of this.watcherDisposables.splice(0)) {
      d.dispose();
    }
    this.watcherFolder = undefined;
    this.pathFilter = undefined;
  }

  private ensureWatcher(folder: string): void {
    if (this.watcherFolder === folder) return;
    this.disposeWatcher();
    this.invalidateCache(true);
    this.watcherFolder = folder;
    const onTreeEvent = (uri: vscode.Uri) => {
      if (uri.scheme !== "file") return;
      const rel = relativePosix(uri.fsPath, folder);
      if (!rel) return;
      if (rel === ".gitignore") {
        this.invalidateCache(true);
        return;
      }
      if (this.pathFilter?.(rel)) return;
      this.invalidateCache();
    };
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, "**/*"));
    this.watcherDisposables.push(
      watcher,
      watcher.onDidCreate(onTreeEvent),
      watcher.onDidDelete(onTreeEvent),
      watcher.onDidChange((uri) => {
        if (uri.scheme === "file" && relativePosix(uri.fsPath, folder) === ".gitignore") {
          this.invalidateCache(true);
        }
      }),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("files.exclude") || event.affectsConfiguration("search.exclude")) {
          this.invalidateCache(true);
        }
      }),
    );
  }

  private async ensureIndex(folder: string): Promise<CachedIndex> {
    this.ensureWatcher(folder);
    if (this.cache && this.cache.folder === folder && Date.now() < this.cache.expiresAt) {
      return this.cache;
    }
    const buildVersion = this.cacheVersion;
    if (
      this.pendingIndex &&
      this.pendingIndex.folder === folder &&
      this.pendingIndex.version === buildVersion
    ) {
      return this.pendingIndex.promise;
    }

    const build = (async (): Promise<CachedIndex> => {
      const folderUri = vscode.Uri.file(folder);
      const baseExcludes = new Set<string>(DEFAULT_MENTION_EXCLUDES);
      collectConfigExcludes(folderUri, baseExcludes);
      const gitignore = await readRootGitignore(folderUri);
      const findFilesExcludes = new Set<string>(baseExcludes);
      for (const pattern of selectSafeFindFilesGitignoreExcludes(gitignore)) {
        findFilesExcludes.add(pattern);
      }
      const exclude = buildFindFilesExcludeGlob(findFilesExcludes);
      const pathFilter = createGitignorePathFilter([...baseExcludes], gitignore);
      const uris = await vscode.workspace.findFiles(
        new vscode.RelativePattern(folder, "**/*"),
        exclude || undefined,
        MAX_INDEX_FILES,
      );
      const files: string[] = [];
      const seen = new Set<string>();
      for (const uri of uris) {
        if (uri.scheme !== "file" || !underFolder(uri.fsPath, folder)) continue;
        const rel = normalizeRelativePath(path.relative(folder, uri.fsPath), "file");
        if (rel && !seen.has(rel) && !pathFilter(rel)) {
          seen.add(rel);
          files.push(rel);
        }
      }
      files.sort((a, b) => a.localeCompare(b));
      const index: CachedIndex = {
        folder,
        files,
        folders: deriveFolders(files),
        expiresAt: Date.now() + CACHE_TTL_MS,
      };
      if (this.watcherFolder === folder && this.cacheVersion === buildVersion) {
        this.pathFilter = pathFilter;
        this.cache = index;
      }
      return index;
    })().finally(() => {
      if (this.pendingIndex?.promise === build) {
        this.pendingIndex = undefined;
      }
    });

    this.pendingIndex = { folder, version: buildVersion, promise: build };
    return build;
  }
}
