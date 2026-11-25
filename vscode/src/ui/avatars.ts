import { readFile, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import * as vscode from "vscode";

import type { Logger } from "../core/host.js";
import type { AvatarState } from "../core/protocol.js";

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

export interface AccountAvatarDeps {
  getSession?: (
    providerId: string,
    scopes: readonly string[],
    options: { silent: true },
  ) => PromiseLike<AccountSessionLike | undefined>;
  fetch?: typeof globalThis.fetch;
  log?: Logger;
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

/**
 * Silently resolves the signed-in VS Code account's avatar (GitHub or Microsoft) as a `data:` URI.
 * Never prompts the user to sign in (`silent: true` only).
 */
export async function resolveVscodeAccountAvatar(deps: AccountAvatarDeps = {}): Promise<string | undefined> {
  const getSession =
    deps.getSession ??
    (vscode.authentication?.getSession
      ? (providerId, scopes, options) => vscode.authentication.getSession(providerId, scopes, options)
      : undefined);
  if (!getSession) return undefined;

  const fetchFn = deps.fetch ?? globalThis.fetch;

  const ghSession =
    (await getSilentSession(getSession, "github", ["read:user"])) ??
    (await getSilentSession(getSession, "github", ["user:email"])) ??
    (await getSilentSession(getSession, "github", []));

  if (ghSession) {
    try {
      let avatarUrl: string | undefined;
      try {
        const res = await fetchFn("https://api.github.com/user", {
          headers: {
            Authorization: `Bearer ${ghSession.accessToken}`,
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
      if (!avatarUrl && /^\d+$/.test(ghSession.account.id)) {
        avatarUrl = `https://avatars.githubusercontent.com/u/${ghSession.account.id}?s=80`;
      }
      if (avatarUrl) {
        const uri = await fetchImageDataUri(avatarUrl, fetchFn, MAX_ACCOUNT_AVATAR_BYTES, undefined, true);
        if (uri) return uri;
      }
    } catch (error) {
      deps.log?.warn(`Failed to resolve GitHub account avatar: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  const msSession = await getSilentSession(getSession, "microsoft", ["User.Read"]);
  if (msSession) {
    try {
      const uri = await fetchImageDataUri(
        "https://graph.microsoft.com/v1.0/me/photo/$value",
        fetchFn,
        MAX_ACCOUNT_AVATAR_BYTES,
        { Authorization: `Bearer ${msSession.accessToken}` },
        true,
      );
      if (uri) return uri;
    } catch (error) {
      deps.log?.warn(`Failed to resolve Microsoft account avatar: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return undefined;
}

/** Resolves and caches user and assistant avatars for the chat webview. */
export class AvatarProvider implements vscode.Disposable {
  private customCache = new Map<string, string | undefined>();
  private accountCache: { resolved: boolean; value: string | undefined } | undefined;
  private accountPending: Promise<string | undefined> | undefined;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: Logger,
  ) {}

  clearAccountCache(): void {
    this.accountCache = undefined;
    this.accountPending = undefined;
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

  private async resolveAccountCached(): Promise<string | undefined> {
    if (this.accountCache?.resolved) return this.accountCache.value;
    if (!this.accountPending) {
      this.accountPending = resolveVscodeAccountAvatar({ log: this.log })
        .then((value) => {
          this.accountCache = { resolved: true, value };
          return value;
        })
        .catch(() => {
          this.accountCache = { resolved: true, value: undefined };
          return undefined;
        })
        .finally(() => {
          this.accountPending = undefined;
        });
    }
    return this.accountPending;
  }

  async resolve(webview: vscode.Webview): Promise<AvatarState> {
    const config = vscode.workspace.getConfiguration("zycode");
    const rawUser = config.get<string>("userAvatar", "");
    const rawAssistant = config.get<string>("assistantAvatar", "");

    const [customUser, customAssistant] = await Promise.all([
      this.resolveCustomCached(rawUser),
      this.resolveCustomCached(rawAssistant),
    ]);

    const user = customUser ?? (await this.resolveAccountCached());
    const assistant =
      customAssistant ??
      webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "zycode-avatar.png")).toString();

    return {
      ...(user ? { user } : {}),
      ...(assistant ? { assistant } : {}),
    };
  }
}
