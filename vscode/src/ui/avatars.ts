import { mkdir, readdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import * as vscode from "vscode";

import type { Logger } from "../core/host.js";
import { matchModelAvatarPreset, normalizeAvatarPreset, type AvatarPresetId } from "../core/avatar-presets.js";
import type { AvatarRole, AvatarState } from "../core/protocol.js";

export type { AvatarRole };

export const AVATAR_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "ico"] as const;
export const AVATAR_SETTING_KEY: Record<AvatarRole, "userAvatar" | "assistantAvatar"> = {
  user: "userAvatar",
  assistant: "assistantAvatar",
};

const MAX_CUSTOM_AVATAR_BYTES = 2 * 1024 * 1024;
const MAX_CUSTOM_DATA_URI_CHARS = 2_800_000;
const MAX_ACCOUNT_AVATAR_BYTES = 512 * 1024;
const FETCH_TIMEOUT_MS = 5000;

const DATA_IMAGE_RE = /^data:image\/(?:png|jpeg|jpg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$/i;
const URI_SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;
const WIN_DRIVE_RE = /^[A-Za-z]:[\\/]/;

const LOCAL_EXT_MIMES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

const REMOTE_RASTER_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
  "image/x-icon",
  "image/vnd.microsoft.icon",
  "image/avif",
]);

export interface CustomAvatarDeps {
  homedir?: () => string;
  stat?: (filePath: string) => Promise<{ size: number; isFile(): boolean }>;
  readFile?: (filePath: string) => Promise<Uint8Array>;
  fetch?: typeof globalThis.fetch;
  log?: Logger;
}

export interface AccountSessionLike {
  accessToken: string;
  account: { id: string; label?: string };
}

/** The accounts whose picture the user can choose as their avatar. */
export type AccountAvatarProvider = "github" | "microsoft";
/** The value of `zycode.accountAvatar`: `off` (the default), or the account that the user chose. */
export type AccountAvatarSetting = "off" | AccountAvatarProvider;

export const ACCOUNT_AVATAR_SETTING_KEY = "accountAvatar";

export function normalizeAccountAvatar(raw: unknown): AccountAvatarSetting {
  return raw === "github" || raw === "microsoft" ? raw : "off";
}

/** The sign-in provider of VS Code, and the scopes that zycode asks for, for each account. */
const ACCOUNT_SESSIONS: Record<AccountAvatarProvider, { providerId: string; scopes: readonly string[] }> = {
  github: { providerId: "github", scopes: ["read:user"] },
  microsoft: { providerId: "microsoft", scopes: ["User.Read"] },
};

export interface AccountAvatarDeps {
  getSession?: (
    providerId: string,
    scopes: readonly string[],
    options: { silent: true },
  ) => PromiseLike<AccountSessionLike | undefined>;
  fetch?: typeof globalThis.fetch;
  log?: Logger;
}

export interface AccountConsentDeps {
  getSession?: (
    providerId: string,
    scopes: readonly string[],
    options: { createIfNone: true },
  ) => PromiseLike<AccountSessionLike | undefined>;
}

function parseContentType(header: string | null): string {
  if (!header) return "";
  return header.split(";", 1)[0]?.trim().toLowerCase() ?? "";
}

function normalizeRasterMime(mime: string): string | undefined {
  if (!REMOTE_RASTER_MIMES.has(mime)) return undefined;
  return mime === "image/jpg" ? "image/jpeg" : mime;
}

function expandHome(rawPath: string, homedir: () => string): string {
  if (rawPath.startsWith("~/") || rawPath.startsWith("~\\")) {
    return path.join(homedir(), rawPath.slice(2));
  }
  return rawPath;
}

async function fetchImageDataUri(
  url: string,
  fetchFn: typeof globalThis.fetch,
  maxBytes: number,
  headers?: Record<string, string>,
  allowAnyRasterImage = false,
): Promise<string | undefined> {
  const res = await fetchFn(url, {
    ...(headers ? { headers } : {}),
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) return undefined;
  const rawMime = parseContentType(res.headers.get("content-type"));
  const mime = allowAnyRasterImage
    ? rawMime.startsWith("image/") && rawMime !== "image/svg+xml"
      ? rawMime === "image/jpg"
        ? "image/jpeg"
        : rawMime
      : undefined
    : normalizeRasterMime(rawMime);
  if (!mime) return undefined;
  const contentLengthHeader = res.headers.get("content-length");
  if (contentLengthHeader) {
    const declared = Number(contentLengthHeader);
    if (Number.isFinite(declared) && declared > maxBytes) return undefined;
  }
  const buf = new Uint8Array(await res.arrayBuffer());
  if (buf.byteLength === 0 || buf.byteLength > maxBytes) return undefined;
  return `data:${mime};base64,${Buffer.from(buf).toString("base64")}`;
}

/**
 * Resolves a custom avatar setting (`data:image/...`, `https://...`, or a local file path)
 * into a webview-safe `data:` URI, or `undefined` if empty or invalid.
 */
export async function resolveCustomAvatar(raw: string, deps: CustomAvatarDeps = {}): Promise<string | undefined> {
  const trimmed = raw.trim();
  if (trimmed === "") return undefined;

  if (trimmed.startsWith("data:")) {
    if (trimmed.length <= MAX_CUSTOM_DATA_URI_CHARS && DATA_IMAGE_RE.test(trimmed)) {
      return trimmed;
    }
    deps.log?.warn("Ignoring custom avatar: invalid or oversized data:image URI.");
    return undefined;
  }

  if (trimmed.startsWith("https://")) {
    const fetchFn = deps.fetch ?? globalThis.fetch;
    try {
      const uri = await fetchImageDataUri(trimmed, fetchFn, MAX_CUSTOM_AVATAR_BYTES);
      if (!uri) deps.log?.warn(`Ignoring custom avatar URL (unsupported or oversized image): ${trimmed}`);
      return uri;
    } catch (error) {
      deps.log?.warn(`Failed to fetch custom avatar URL: ${error instanceof Error ? error.message : String(error)}`);
      return undefined;
    }
  }

  if (URI_SCHEME_RE.test(trimmed) && !WIN_DRIVE_RE.test(trimmed)) {
    deps.log?.warn(`Ignoring custom avatar with unsupported URI scheme: ${trimmed}`);
    return undefined;
  }

  const homedirFn = deps.homedir ?? os.homedir;
  const statFn = deps.stat ?? stat;
  const readFileFn = deps.readFile ?? readFile;
  const resolved = path.resolve(expandHome(trimmed, homedirFn));
  const ext = path.extname(resolved).toLowerCase();
  const mime = LOCAL_EXT_MIMES[ext];
  if (!mime) {
    deps.log?.warn(`Ignoring custom avatar with unsupported extension: ${resolved}`);
    return undefined;
  }

  try {
    const info = await statFn(resolved);
    if (!info.isFile() || info.size <= 0 || info.size > MAX_CUSTOM_AVATAR_BYTES) {
      deps.log?.warn(`Ignoring custom avatar file (missing, not a file, or > 2 MB): ${resolved}`);
      return undefined;
    }
    const data = await readFileFn(resolved);
    if (data.byteLength === 0 || data.byteLength > MAX_CUSTOM_AVATAR_BYTES) {
      deps.log?.warn(`Ignoring custom avatar file (empty or > 2 MB): ${resolved}`);
      return undefined;
    }
    return `data:${mime};base64,${Buffer.from(data).toString("base64")}`;
  } catch (error) {
    deps.log?.warn(`Could not read custom avatar file ${resolved}: ${error instanceof Error ? error.message : String(error)}`);
    return undefined;
  }
}

async function getSilentSession(
  getSession: NonNullable<AccountAvatarDeps["getSession"]>,
  providerId: string,
  scopes: readonly string[],
): Promise<AccountSessionLike | undefined> {
  try {
    return await getSession(providerId, scopes, { silent: true });
  } catch {
    return undefined;
  }
}

async function fetchGithubAvatar(
  session: AccountSessionLike,
  fetchFn: typeof globalThis.fetch,
  log: Logger | undefined,
): Promise<string | undefined> {
  try {
    let avatarUrl: string | undefined;
    try {
      // The sign-in token goes to `api.github.com` and nowhere else.
      const res = await fetchFn("https://api.github.com/user", {
        headers: {
          Authorization: `Bearer ${session.accessToken}`,
          "User-Agent": "zycode-vscode",
        },
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      if (res.ok) {
        const json = (await res.json()) as { avatar_url?: unknown };
        if (typeof json.avatar_url === "string" && json.avatar_url.startsWith("https://")) {
          avatarUrl = json.avatar_url;
        }
      }
    } catch {
      // Fall through to account.id avatar URL if applicable.
    }
    if (!avatarUrl && /^\d+$/.test(session.account.id)) {
      avatarUrl = `https://avatars.githubusercontent.com/u/${session.account.id}?s=80`;
    }
    if (avatarUrl) {
      // The picture itself is a plain download: no token goes with it.
      return await fetchImageDataUri(avatarUrl, fetchFn, MAX_ACCOUNT_AVATAR_BYTES, undefined, true);
    }
  } catch (error) {
    log?.warn(`Failed to resolve GitHub account avatar: ${error instanceof Error ? error.message : String(error)}`);
  }
  return undefined;
}

async function fetchMicrosoftAvatar(
  session: AccountSessionLike,
  fetchFn: typeof globalThis.fetch,
  log: Logger | undefined,
): Promise<string | undefined> {
  try {
    // The sign-in token goes to `graph.microsoft.com` and nowhere else.
    return await fetchImageDataUri(
      "https://graph.microsoft.com/v1.0/me/photo/$value",
      fetchFn,
      MAX_ACCOUNT_AVATAR_BYTES,
      { Authorization: `Bearer ${session.accessToken}` },
      true,
    );
  } catch (error) {
    log?.warn(`Failed to resolve Microsoft account avatar: ${error instanceof Error ? error.message : String(error)}`);
  }
  return undefined;
}

/**
 * Resolves the picture of the account that the user chose (`zycode.accountAvatar`) as a `data:` URI.
 * It never asks the user to sign in and never shows a badge (`silent: true` only): VS Code gives a
 * session only to an account that the user already allowed `zycode` to use, in
 * {@link requestAccountSession}. With no such session, it logs a hint and returns `undefined`.
 */
export async function resolveVscodeAccountAvatar(
  provider: AccountAvatarProvider,
  deps: AccountAvatarDeps = {},
): Promise<string | undefined> {
  const getSession =
    deps.getSession ??
    (vscode.authentication?.getSession
      ? (providerId, scopes, options) => vscode.authentication.getSession(providerId, scopes, options)
      : undefined);
  if (!getSession) return undefined;

  const { providerId, scopes } = ACCOUNT_SESSIONS[provider];
  const session = await getSilentSession(getSession, providerId, scopes);
  if (!session) {
    deps.log?.info(
      `The account avatar is set to ${provider}, but VS Code has no ${provider} session that zycode may use. ` +
        'Run "zycode: Use account picture as avatar" to allow it, or set zycode.accountAvatar to "off".',
    );
    return undefined;
  }

  const fetchFn = deps.fetch ?? globalThis.fetch;
  return provider === "github"
    ? fetchGithubAvatar(session, fetchFn, deps.log)
    : fetchMicrosoftAvatar(session, fetchFn, deps.log);
}

/**
 * Asks VS Code for a session of `provider`. VS Code shows its own question: to sign in, or to allow
 * `zycode` to use an account that is already signed in. Call it only for a choice that the user just
 * made (the command); a lookup that runs by itself must use {@link resolveVscodeAccountAvatar}.
 * Returns `false` when the user says no, or when VS Code has no such provider.
 */
export async function requestAccountSession(
  provider: AccountAvatarProvider,
  deps: AccountConsentDeps = {},
): Promise<boolean> {
  const getSession =
    deps.getSession ??
    (vscode.authentication?.getSession
      ? (providerId, scopes, options) => vscode.authentication.getSession(providerId, scopes, options)
      : undefined);
  if (!getSession) return false;

  const { providerId, scopes } = ACCOUNT_SESSIONS[provider];
  try {
    return (await getSession(providerId, scopes, { createIfNone: true })) !== undefined;
  } catch {
    return false;
  }
}

export interface ImportAvatarDeps {
  stat?: (filePath: string) => Promise<{ size: number; isFile(): boolean }>;
  readFile?: (filePath: string) => Promise<Uint8Array>;
  mkdir?: (dirPath: string, options: { recursive: boolean }) => Promise<unknown>;
  writeFile?: (filePath: string, data: Uint8Array) => Promise<void>;
  readdir?: (dirPath: string) => Promise<string[]>;
  unlink?: (filePath: string) => Promise<void>;
  now?: () => number;
}

/**
 * Removes stored avatar files for `role` inside `<storageDir>/avatars`, keeping `keepName` if given.
 * Silently ignores missing directories or unlink errors.
 */
export async function cleanupStoredAvatars(
  role: AvatarRole,
  storageDir: string | undefined,
  deps: Pick<ImportAvatarDeps, "readdir" | "unlink"> = {},
  keepName?: string,
): Promise<void> {
  if (!storageDir) return;
  const avatarsDir = path.join(storageDir, "avatars");
  const readdirFn = deps.readdir ?? readdir;
  const unlinkFn = deps.unlink ?? unlink;
  try {
    const entries = await readdirFn(avatarsDir);
    const prefix = `${role}-`;
    await Promise.all(
      entries
        .filter((name) => name.startsWith(prefix) && name !== keepName)
        .map((name) => unlinkFn(path.join(avatarsDir, name)).catch(() => undefined)),
    );
  } catch {
    // Ignore missing directory or read errors during cleanup.
  }
}

/**
 * Validates a local image file and copies it into `<storageDir>/avatars/<role>-<ts><ext>` (when
 * `storageDir` is provided), removing previous copies for the same role.
 */
export async function importLocalAvatarFile(
  sourcePath: string,
  role: AvatarRole,
  storageDir: string | undefined,
  deps: ImportAvatarDeps = {},
): Promise<{ ok: true; path: string } | { ok: false; reason: string }> {
  const resolvedSource = path.resolve(sourcePath);
  const ext = path.extname(resolvedSource).toLowerCase();
  if (!LOCAL_EXT_MIMES[ext]) {
    return { ok: false, reason: "Unsupported image format. Choose PNG, JPG, GIF, WebP, SVG, or ICO." };
  }

  const statFn = deps.stat ?? stat;
  const readFileFn = deps.readFile ?? readFile;
  let data: Uint8Array;
  try {
    const info = await statFn(resolvedSource);
    if (!info.isFile() || info.size <= 0 || info.size > MAX_CUSTOM_AVATAR_BYTES) {
      return { ok: false, reason: "Image is empty, unreadable, or larger than 2 MB." };
    }
    data = await readFileFn(resolvedSource);
    if (data.byteLength === 0 || data.byteLength > MAX_CUSTOM_AVATAR_BYTES) {
      return { ok: false, reason: "Image is empty, unreadable, or larger than 2 MB." };
    }
  } catch {
    return { ok: false, reason: "Could not read the selected image file." };
  }

  if (!storageDir) {
    return { ok: true, path: resolvedSource };
  }

  const avatarsDir = path.join(storageDir, "avatars");
  const mkdirFn = deps.mkdir ?? mkdir;
  const writeFileFn = deps.writeFile ?? writeFile;
  const nowFn = deps.now ?? Date.now;
  const fileName = `${role}-${nowFn()}${ext}`;
  const targetPath = path.join(avatarsDir, fileName);

  try {
    await mkdirFn(avatarsDir, { recursive: true });
    await writeFileFn(targetPath, data);
  } catch (error) {
    // A full disk or a folder with no write access must give the user a message, not an unhandled rejection.
    return {
      ok: false,
      reason: `Could not save a copy of the image in the storage folder of the extension (${error instanceof Error ? error.message : String(error)}).`,
    };
  }
  await cleanupStoredAvatars(role, storageDir, deps, fileName);
  return { ok: true, path: targetPath };
}

/** Resolves and caches user and assistant avatars for the chat webview. */
export class AvatarProvider implements vscode.Disposable {
  private customCache = new Map<string, string | undefined>();
  /** What the last lookup of the chosen account found (`undefined` when it found no picture). */
  private accountCache: { provider: AccountAvatarProvider; value: string | undefined } | undefined;
  /** The account that a lookup in the background is for, while it runs. */
  private accountLookup: AccountAvatarProvider | undefined;
  /** Grows when the account cache is cleared, so a lookup that began before then stores nothing. */
  private accountEpoch = 0;
  private activeModelIds: [string | undefined, string | undefined] = [undefined, undefined];

  /**
   * @param onAccountAvatar called when a lookup in the background found the account picture: the
   *   caller posts the avatars to the chat again.
   * @param accountDeps replaces the sign-in and the network calls of the account lookup (for tests).
   */
  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: Logger,
    private readonly onAccountAvatar: () => void = () => undefined,
    private readonly accountDeps: Pick<AccountAvatarDeps, "getSession" | "fetch"> = {},
  ) {}

  setActiveModel(modelId?: string, modelName?: string): boolean {
    const nextId = modelId?.trim() || undefined;
    const nextName = modelName?.trim() || undefined;
    if (this.activeModelIds[0] === nextId && this.activeModelIds[1] === nextName) {
      return false;
    }
    this.activeModelIds = [nextId, nextName];
    return true;
  }

  private presetUri(webview: vscode.Webview, preset: AvatarPresetId): string {
    return webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "avatars", `${preset}.png`)).toString();
  }

  clearCustomCache(): void {
    this.customCache.clear();
  }

  clearAccountCache(): void {
    this.accountEpoch += 1;
    this.accountCache = undefined;
    this.accountLookup = undefined;
  }

  dispose(): void {
    this.customCache.clear();
    this.clearAccountCache();
  }

  private async resolveCustomCached(raw: string): Promise<string | undefined> {
    const trimmed = raw.trim();
    if (trimmed === "") return undefined;
    if (this.customCache.has(trimmed)) return this.customCache.get(trimmed);
    const resolved = await resolveCustomAvatar(trimmed, { log: this.log });
    if (this.customCache.size >= 16) this.customCache.clear();
    this.customCache.set(trimmed, resolved);
    return resolved;
  }

  /**
   * The picture of `provider` if a lookup already found it. With no answer yet, it starts a lookup in the
   * background and returns `undefined` at once: a slow network must not hold back the other avatars.
   * `onAccountAvatar` runs when the picture arrives.
   */
  private accountPicture(provider: AccountAvatarProvider): string | undefined {
    if (this.accountCache?.provider === provider) return this.accountCache.value;
    if (this.accountLookup !== provider) this.lookUpAccount(provider);
    return undefined;
  }

  private lookUpAccount(provider: AccountAvatarProvider): void {
    const epoch = this.accountEpoch;
    this.accountLookup = provider;
    void resolveVscodeAccountAvatar(provider, { ...this.accountDeps, log: this.log })
      .catch(() => undefined)
      .then((value) => {
        if (epoch !== this.accountEpoch) return;
        this.accountLookup = undefined;
        this.accountCache = { provider, value };
        if (value !== undefined) this.onAccountAvatar();
      });
  }

  async resolve(webview: vscode.Webview): Promise<AvatarState> {
    const config = vscode.workspace.getConfiguration("zycode");
    const rawUser = config.get<string>("userAvatar", "");
    const rawAssistant = config.get<string>("assistantAvatar", "");
    const rawPreset = config.get<string>("assistantAvatarPreset", "claude");
    const autoModelAvatar = config.get<boolean>("autoModelAvatar", true);
    const accountAvatar = normalizeAccountAvatar(config.get<string>(ACCOUNT_AVATAR_SETTING_KEY, "off"));

    const matchedPreset = autoModelAvatar ? matchModelAvatarPreset(...this.activeModelIds) : undefined;
    const [customUser, customAssistant] = await Promise.all([
      this.resolveCustomCached(rawUser),
      matchedPreset === undefined ? this.resolveCustomCached(rawAssistant) : Promise.resolve(undefined),
    ]);

    // The account picture is off until the user turns it on (`zycode.accountAvatar`), and no lookup
    // starts while an image of their own is set. A lookup can be slow, so the default icon shows
    // first and the picture follows when it arrives.
    const user = customUser ?? (accountAvatar === "off" ? undefined : this.accountPicture(accountAvatar));
    const assistant =
      matchedPreset !== undefined
        ? this.presetUri(webview, matchedPreset)
        : customAssistant ?? this.presetUri(webview, normalizeAvatarPreset(rawPreset));

    return {
      ...(user ? { user } : {}),
      ...(assistant ? { assistant } : {}),
    };
  }
}
