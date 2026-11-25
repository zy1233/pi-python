import { existsSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

const mockConfig = vi.hoisted(() => ({
  values: {} as Record<string, unknown>,
}));

vi.mock("vscode", () => ({
  authentication: { getSession: vi.fn(async () => undefined) },
  workspace: {
    getConfiguration: () => ({
      get: (k: string, d?: unknown) => (k in mockConfig.values ? mockConfig.values[k] : d),
    }),
  },
  Uri: {
    joinPath: (base: { fsPath: string }, ...segs: string[]) => {
      const joined = path.posix.join(base.fsPath.replace(/\\/g, "/"), ...segs);
      return { fsPath: joined, toString: () => `file://${joined}` };
    },
  },
}));

import { matchModelAvatarPreset } from "../src/core/avatar-presets.js";
import {
  AvatarProvider,
  cleanupStoredAvatars,
  importLocalAvatarFile,
  normalizeAccountAvatar,
  requestAccountSession,
  resolveCustomAvatar,
  resolveVscodeAccountAvatar,
} from "../src/ui/avatars.js";

type FetchInput = Parameters<typeof globalThis.fetch>[0];

describe("resolveCustomAvatar", () => {
  it("returns undefined for empty input", async () => {
    expect(await resolveCustomAvatar("")).toBeUndefined();
    expect(await resolveCustomAvatar("   ")).toBeUndefined();
  });

  it("accepts valid data:image URIs and rejects non-image or malformed data URIs", async () => {
    const valid = "data:image/png;base64,iVBORw0KGgo=";
    expect(await resolveCustomAvatar(valid)).toBe(valid);
    expect(await resolveCustomAvatar("data:text/html;base64,PHNjcmlwdD4=")).toBeUndefined();
    expect(await resolveCustomAvatar("data:image/png;base64,not valid!")).toBeUndefined();
  });

  it("reads local image files and expands ~/", async () => {
    const pngBytes = new Uint8Array([137, 80, 78, 71]);
    const svgBytes = new TextEncoder().encode("<svg></svg>");
    const home = path.resolve("/home/tester");

    const fromHome = await resolveCustomAvatar("~/avatar.png", {
      homedir: () => home,
      stat: async (p) => {
        expect(p).toBe(path.resolve(path.join(home, "avatar.png")));
        return { size: pngBytes.byteLength, isFile: () => true };
      },
      readFile: async () => pngBytes,
    });
    expect(fromHome).toBe(`data:image/png;base64,${Buffer.from(pngBytes).toString("base64")}`);

    const fromSvg = await resolveCustomAvatar(path.join(home, "logo.svg"), {
      stat: async () => ({ size: svgBytes.byteLength, isFile: () => true }),
      readFile: async () => svgBytes,
    });
    expect(fromSvg).toBe(`data:image/svg+xml;base64,${Buffer.from(svgBytes).toString("base64")}`);
  });

  it("rejects oversized, missing, or unsupported local files", async () => {
    expect(
      await resolveCustomAvatar("/tmp/huge.png", {
        stat: async () => ({ size: 3 * 1024 * 1024, isFile: () => true }),
      }),
    ).toBeUndefined();

    expect(
      await resolveCustomAvatar("/tmp/missing.png", {
        stat: async () => {
          throw new Error("ENOENT");
        },
      }),
    ).toBeUndefined();

    expect(await resolveCustomAvatar("/tmp/notes.txt")).toBeUndefined();
  });

  it("fetches https:// raster images as data URIs and rejects http://, file://, and remote SVG", async () => {
    expect(await resolveCustomAvatar("http://example.com/a.png")).toBeUndefined();
    expect(await resolveCustomAvatar("file:///etc/passwd")).toBeUndefined();

    const pngBytes = new Uint8Array([1, 2, 3, 4]);
    const ok = await resolveCustomAvatar("https://example.com/avatar.png", {
      fetch: vi.fn(async () => new Response(pngBytes, { status: 200, headers: { "content-type": "image/png" } })),
    });
    expect(ok).toBe(`data:image/png;base64,${Buffer.from(pngBytes).toString("base64")}`);

    const remoteSvg = await resolveCustomAvatar("https://example.com/avatar.svg", {
      fetch: vi.fn(async () => new Response("<svg/>", { status: 200, headers: { "content-type": "image/svg+xml" } })),
    });
    expect(remoteSvg).toBeUndefined();
  });

  it("treats Windows drive paths as local files rather than URI schemes", async () => {
    const expected = path.resolve(`C:${path.sep}Users${path.sep}me${path.sep}a.png`);
    if (os.platform() === "win32") {
      let seenPath = "";
      await resolveCustomAvatar("C:\\Users\\me\\a.png", {
        stat: async (p) => {
          seenPath = p;
          return { size: 4, isFile: () => true };
        },
        readFile: async () => new Uint8Array([1, 2, 3, 4]),
      });
      expect(seenPath).toBe(expected);
    }
  });
});

describe("normalizeAccountAvatar", () => {
  it("accepts the two accounts and reads anything else as off", () => {
    expect(normalizeAccountAvatar("github")).toBe("github");
    expect(normalizeAccountAvatar("microsoft")).toBe("microsoft");
    for (const raw of ["off", "", "GitHub", "gitlab", undefined, null, 1, {}]) {
      expect(normalizeAccountAvatar(raw), String(raw)).toBe("off");
    }
  });
});

describe("resolveVscodeAccountAvatar", () => {
  it("asks only the account that was chosen, only silently, and writes a hint when VS Code has no session", async () => {
    for (const [provider, scopes] of [
      ["github", ["read:user"]],
      ["microsoft", ["User.Read"]],
    ] as const) {
      const calls: { providerId: string; scopes: readonly string[]; options: object }[] = [];
      const infos: string[] = [];
      const out = await resolveVscodeAccountAvatar(provider, {
        getSession: async (providerId, requested, options) => {
          calls.push({ providerId, scopes: requested, options });
          return undefined;
        },
        fetch: vi.fn(async () => {
          throw new Error("no request may be made without a session");
        }),
        log: { info: (text: string) => infos.push(text), warn() {}, error() {} },
      });
      expect(out).toBeUndefined();
      // One call, for the chosen account. `silent: true` shows no question and no badge; `createIfNone` would ask.
      expect(calls).toEqual([{ providerId: provider, scopes, options: { silent: true } }]);
      expect(infos).toHaveLength(1);
      expect(infos[0]).toContain(provider);
      expect(infos[0]).toContain("zycode.accountAvatar");
    }
  });

  it("treats a session lookup that throws as no session", async () => {
    const out = await resolveVscodeAccountAvatar("github", {
      getSession: async () => {
        throw new Error("provider not available");
      },
    });
    expect(out).toBeUndefined();
  });

  it("sends the token to the API of the chosen account and to no other server", async () => {
    const imgBytes = new Uint8Array([7, 7, 7]);
    const seen: { url: string; authorization: string | undefined }[] = [];
    const record = (input: FetchInput, init?: RequestInit): void => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      seen.push({ url: String(input), authorization: headers.Authorization });
    };

    await resolveVscodeAccountAvatar("github", {
      getSession: async () => ({ accessToken: "gh-secret", account: { id: "42" } }),
      fetch: vi.fn(async (input, init) => {
        record(input, init);
        if (String(input) === "https://api.github.com/user") {
          return new Response(JSON.stringify({ avatar_url: "https://avatars.githubusercontent.com/u/42?v=4" }), { status: 200 });
        }
        return new Response(imgBytes, { status: 200, headers: { "content-type": "image/png" } });
      }),
    });
    expect(seen).toEqual([
      { url: "https://api.github.com/user", authorization: "Bearer gh-secret" },
      { url: "https://avatars.githubusercontent.com/u/42?v=4", authorization: undefined },
    ]);

    seen.length = 0;
    await resolveVscodeAccountAvatar("microsoft", {
      getSession: async () => ({ accessToken: "ms-secret", account: { id: "ms-1" } }),
      fetch: vi.fn(async (input, init) => {
        record(input, init);
        return new Response(imgBytes, { status: 200, headers: { "content-type": "image/jpeg" } });
      }),
    });
    expect(seen).toEqual([{ url: "https://graph.microsoft.com/v1.0/me/photo/$value", authorization: "Bearer ms-secret" }]);
  });

  it("does not fall back to the other account when the chosen one gives no picture", async () => {
    const asked: string[] = [];
    const out = await resolveVscodeAccountAvatar("github", {
      getSession: async (providerId) => {
        asked.push(providerId);
        return providerId === "microsoft" ? { accessToken: "ms", account: { id: "ms-1" } } : undefined;
      },
      fetch: vi.fn(async () => new Response("no", { status: 404 })),
    });
    expect(out).toBeUndefined();
    expect(asked).toEqual(["github"]);
  });

  it("resolves GitHub avatar via /user API and falls back to avatars.githubusercontent.com/u/<id>", async () => {
    const imgBytes = new Uint8Array([10, 20, 30]);
    const expected = `data:image/png;base64,${Buffer.from(imgBytes).toString("base64")}`;

    const viaApi = await resolveVscodeAccountAvatar("github", {
      getSession: async (providerId) =>
        providerId === "github" ? { accessToken: "tok", account: { id: "42", label: "octo" } } : undefined,
      fetch: vi.fn(async (input) => {
        const url = String(input);
        if (url === "https://api.github.com/user") {
          return new Response(JSON.stringify({ avatar_url: "https://avatars.githubusercontent.com/u/42?v=4" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response(imgBytes, { status: 200, headers: { "content-type": "image/png" } });
      }),
    });
    expect(viaApi).toBe(expected);

    const fetchedUrls: string[] = [];
    const viaIdFallback = await resolveVscodeAccountAvatar("github", {
      getSession: async (providerId) =>
        providerId === "github" ? { accessToken: "tok", account: { id: "99", label: "octo" } } : undefined,
      fetch: vi.fn(async (input) => {
        const url = String(input);
        fetchedUrls.push(url);
        if (url === "https://api.github.com/user") {
          return new Response("forbidden", { status: 403 });
        }
        return new Response(imgBytes, { status: 200, headers: { "content-type": "image/png" } });
      }),
    });
    expect(viaIdFallback).toBe(expected);
    expect(fetchedUrls).toContain("https://avatars.githubusercontent.com/u/99?s=80");
  });

  it("resolves the Microsoft Graph photo when Microsoft is the chosen account", async () => {
    const jpgBytes = new Uint8Array([255, 216, 255]);
    const out = await resolveVscodeAccountAvatar("microsoft", {
      getSession: async (providerId) =>
        providerId === "microsoft" ? { accessToken: "ms-tok", account: { id: "ms-1", label: "user@contoso.com" } } : undefined,
      fetch: vi.fn(async (input) => {
        expect(String(input)).toBe("https://graph.microsoft.com/v1.0/me/photo/$value");
        return new Response(jpgBytes, { status: 200, headers: { "content-type": "image/jpeg" } });
      }),
    });
    expect(out).toBe(`data:image/jpeg;base64,${Buffer.from(jpgBytes).toString("base64")}`);
  });
});

describe("requestAccountSession", () => {
  it("asks VS Code with createIfNone for the scopes of the chosen account, and is true only with a session", async () => {
    const calls: { providerId: string; scopes: readonly string[]; options: object }[] = [];
    const answer = { accessToken: "t", account: { id: "1" } };
    const getSession = async (providerId: string, scopes: readonly string[], options: { createIfNone: true }) => {
      calls.push({ providerId, scopes, options });
      return answer;
    };
    expect(await requestAccountSession("github", { getSession })).toBe(true);
    expect(await requestAccountSession("microsoft", { getSession })).toBe(true);
    expect(calls).toEqual([
      { providerId: "github", scopes: ["read:user"], options: { createIfNone: true } },
      { providerId: "microsoft", scopes: ["User.Read"], options: { createIfNone: true } },
    ]);
  });

  it("is false when the user refuses or closes the question, or when VS Code rejects the call", async () => {
    expect(await requestAccountSession("github", { getSession: async () => undefined })).toBe(false);
    expect(
      await requestAccountSession("microsoft", {
        getSession: async () => {
          throw new Error("User did not consent to login.");
        },
      }),
    ).toBe(false);
  });
});

describe("importLocalAvatarFile & cleanupStoredAvatars", () => {
  it("copies valid images into storageDir/avatars, cleans up old files for the same role, and leaves other roles intact", async () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "zycode-avatar-import-"));
    try {
      const storageDir = path.join(tmp, "globalStorage");
      const src1 = path.join(tmp, "pic1.png");
      const src2 = path.join(tmp, "pic2.jpg");
      const srcAst = path.join(tmp, "bot.png");
      writeFileSync(src1, Buffer.from([137, 80, 78, 71, 1]));
      writeFileSync(src2, Buffer.from([255, 216, 255, 2]));
      writeFileSync(srcAst, Buffer.from([137, 80, 78, 71, 3]));

      const astRes = await importLocalAvatarFile(srcAst, "assistant", storageDir, { now: () => 1000 });
      expect(astRes).toEqual({ ok: true, path: path.join(storageDir, "avatars", "assistant-1000.png") });
      expect(existsSync(astRes.ok ? astRes.path : "")).toBe(true);

      const res1 = await importLocalAvatarFile(src1, "user", storageDir, { now: () => 2000 });
      expect(res1).toEqual({ ok: true, path: path.join(storageDir, "avatars", "user-2000.png") });

      const res2 = await importLocalAvatarFile(src2, "user", storageDir, { now: () => 3000 });
      expect(res2).toEqual({ ok: true, path: path.join(storageDir, "avatars", "user-3000.jpg") });

      const files = readdirSync(path.join(storageDir, "avatars")).sort();
      expect(files).toEqual(["assistant-1000.png", "user-3000.jpg"]);

      await cleanupStoredAvatars("user", storageDir);
      expect(readdirSync(path.join(storageDir, "avatars"))).toEqual(["assistant-1000.png"]);

      // Fallback when storageDir is undefined returns resolved source path.
      const direct = await importLocalAvatarFile(src1, "user", undefined);
      expect(direct).toEqual({ ok: true, path: path.resolve(src1) });
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("reports a failed copy (no folder, no write access, full disk) instead of throwing", async () => {
    const readable = {
      stat: async () => ({ size: 4, isFile: () => true }),
      readFile: async () => new Uint8Array([137, 80, 78, 71]),
    };
    const noFolder = await importLocalAvatarFile("/tmp/ok.png", "user", "/tmp/store", {
      ...readable,
      mkdir: async () => {
        throw new Error("EACCES: permission denied, mkdir '/tmp/store/avatars'");
      },
    });
    expect(noFolder.ok).toBe(false);
    expect(noFolder.ok ? "" : noFolder.reason).toContain("Could not save a copy");
    expect(noFolder.ok ? "" : noFolder.reason).toContain("EACCES");

    let removed = 0;
    const fullDisk = await importLocalAvatarFile("/tmp/ok.png", "user", "/tmp/store", {
      ...readable,
      mkdir: async () => undefined,
      writeFile: async () => {
        throw new Error("ENOSPC: no space left on device");
      },
      readdir: async () => ["user-1.png"],
      unlink: async () => {
        removed += 1;
      },
    });
    expect(fullDisk.ok).toBe(false);
    expect(fullDisk.ok ? "" : fullDisk.reason).toContain("ENOSPC");
    // The old copy stays: the user still has an avatar when the new one cannot be saved.
    expect(removed).toBe(0);
  });

  it("rejects unsupported extensions, oversized files, and missing files", async () => {
    expect(await importLocalAvatarFile("/tmp/bad.txt", "user", "/tmp/store")).toMatchObject({ ok: false });
    expect(
      await importLocalAvatarFile("/tmp/huge.png", "user", "/tmp/store", {
        stat: async () => ({ size: 3 * 1024 * 1024, isFile: () => true }),
      }),
    ).toMatchObject({ ok: false });
    expect(
      await importLocalAvatarFile("/tmp/missing.png", "user", "/tmp/store", {
        stat: async () => {
          throw new Error("ENOENT");
        },
      }),
    ).toMatchObject({ ok: false });
  });
});

describe("matchModelAvatarPreset & AvatarProvider", () => {
  it("matches all 7 mascot families across direct and gateway model names", () => {
    expect(matchModelAvatarPreset("auto/deepseek-v4.1-flash")).toBe("deepseek");
    expect(matchModelAvatarPreset("deepseek-reasoner")).toBe("deepseek");
    expect(matchModelAvatarPreset("openrouter/google/gemini-2.5-pro")).toBe("gemini");
    expect(matchModelAvatarPreset("gemma-3-27b")).toBe("gemini");
    expect(matchModelAvatarPreset("anthropic/claude-sonnet-4")).toBe("claude");
    expect(matchModelAvatarPreset("x-ai/grok-3-mini")).toBe("grok");
    expect(matchModelAvatarPreset("moonshot-v1-128k")).toBe("kimi");
    expect(matchModelAvatarPreset("kimi-k2-instruct")).toBe("kimi");
    expect(matchModelAvatarPreset("qwen2.5-coder-32b")).toBe("qwen");
    expect(matchModelAvatarPreset("qwq-32b")).toBe("qwen");
    expect(matchModelAvatarPreset("gpt-4.1-mini")).toBe("openai");
    expect(matchModelAvatarPreset("openai/o3-mini")).toBe("openai");
    expect(matchModelAvatarPreset(undefined, "Gemini 2.5 Flash")).toBe("gemini");
    expect(matchModelAvatarPreset("custom-llama-3")).toBeUndefined();
  });

  it("resolves assistant avatar by active model when autoModelAvatar is on, and respects user overrides when off or unmatched", async () => {
    const provider = new AvatarProvider(
      { fsPath: "/ext" } as never,
      { info() {}, warn() {}, error() {} },
    );
    const webview = {
      asWebviewUri: (uri: { toString(): string }) => ({ toString: () => `webview:${uri.toString()}` }),
    };

    try {
      const customUri = "data:image/png;base64,iVBORw0KGgo=";
      mockConfig.values = {
        autoModelAvatar: true,
        assistantAvatarPreset: "kimi",
        assistantAvatar: customUri,
      };

      expect(provider.setActiveModel("auto/deepseek-v4.1-flash", "deepseek")).toBe(true);
      expect(provider.setActiveModel("auto/deepseek-v4.1-flash", "deepseek")).toBe(false);
      expect(await provider.resolve(webview as never)).toEqual({
        assistant: "webview:file:///ext/media/avatars/deepseek.png",
      });

      // Unrecognized model falls back to custom assistantAvatar, then assistantAvatarPreset.
      expect(provider.setActiveModel("my-local-model")).toBe(true);
      expect(await provider.resolve(webview as never)).toEqual({
        assistant: customUri,
      });

      mockConfig.values.assistantAvatar = "";
      expect(await provider.resolve(webview as never)).toEqual({
        assistant: "webview:file:///ext/media/avatars/kimi.png",
      });

      // With autoModelAvatar disabled, active model is ignored.
      expect(provider.setActiveModel("gemini-2.5-pro")).toBe(true);
      mockConfig.values.autoModelAvatar = false;
      mockConfig.values.assistantAvatar = customUri;
      expect(await provider.resolve(webview as never)).toEqual({
        assistant: customUri,
      });

      mockConfig.values.assistantAvatar = "";
      mockConfig.values.assistantAvatarPreset = "qwen";
      expect(await provider.resolve(webview as never)).toEqual({
        assistant: "webview:file:///ext/media/avatars/qwen.png",
      });
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });
});

describe("AvatarProvider: the account picture is an opt-in", () => {
  const log = { info() {}, warn() {}, error() {} };
  const webview = {
    asWebviewUri: (uri: { toString(): string }) => ({ toString: () => `webview:${uri.toString()}` }),
  };
  const png = new Uint8Array([9, 8, 7]);
  const pngUri = `data:image/png;base64,${Buffer.from(png).toString("base64")}`;
  const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
  const fetchPicture = vi.fn(async (input: FetchInput) =>
    String(input) === "https://api.github.com/user"
      ? new Response(JSON.stringify({ avatar_url: "https://avatars.githubusercontent.com/u/1?v=4" }), { status: 200 })
      : new Response(png, { status: 200, headers: { "content-type": "image/png" } }),
  );
  const session = { accessToken: "t", account: { id: "1" } };

  function setup(
    getSession: (providerId: string, scopes: readonly string[], options: { silent: true }) => Promise<typeof session | undefined>,
  ) {
    const arrived = vi.fn();
    const provider = new AvatarProvider({ fsPath: "/ext" } as never, log, arrived, { getSession, fetch: fetchPicture });
    return { provider, arrived };
  }

  function gate() {
    let release!: () => void;
    const opened = new Promise<void>((resolve) => {
      release = resolve;
    });
    return { opened, release };
  }

  it("makes no request while the setting is off, empty or not a known account (the default)", async () => {
    fetchPicture.mockClear();
    const getSession = vi.fn(async () => session);
    const { provider, arrived } = setup(getSession);
    try {
      for (const values of [{}, { accountAvatar: "off" }, { accountAvatar: "gitlab" }, { accountAvatar: 7 }]) {
        mockConfig.values = values;
        expect((await provider.resolve(webview as never)).user).toBeUndefined();
      }
      await settle();
      expect(getSession).not.toHaveBeenCalled();
      expect(fetchPicture).not.toHaveBeenCalled();
      expect(arrived).not.toHaveBeenCalled();
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });

  it("shows the plain icon at once, looks up the picture in the background, and tells the caller when it arrives", async () => {
    mockConfig.values = { accountAvatar: "github" };
    const { opened, release } = gate();
    const getSession = vi.fn(async () => {
      await opened;
      return session;
    });
    const { provider, arrived } = setup(getSession);
    try {
      // `resolve` returns while the lookup still waits: a slow network cannot hold back the chat.
      expect((await provider.resolve(webview as never)).user).toBeUndefined();
      expect(getSession).toHaveBeenCalledTimes(1);
      // A second call while the lookup runs does not start another one.
      expect((await provider.resolve(webview as never)).user).toBeUndefined();
      expect(getSession).toHaveBeenCalledTimes(1);

      release();
      await settle();
      expect(arrived).toHaveBeenCalledTimes(1);
      expect((await provider.resolve(webview as never)).user).toBe(pngUri);
      expect(getSession).toHaveBeenCalledTimes(1);
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });

  it("remembers that VS Code gave no picture, and looks again only after the cache is cleared", async () => {
    mockConfig.values = { accountAvatar: "microsoft" };
    const getSession = vi.fn(async () => undefined);
    const { provider, arrived } = setup(getSession);
    try {
      await provider.resolve(webview as never);
      await settle();
      await provider.resolve(webview as never);
      expect(getSession).toHaveBeenCalledTimes(1);
      expect(arrived).not.toHaveBeenCalled();

      provider.clearAccountCache();
      await provider.resolve(webview as never);
      expect(getSession).toHaveBeenCalledTimes(2);
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });

  it("drops the answer of a lookup that began before a clear", async () => {
    mockConfig.values = { accountAvatar: "github" };
    const { opened, release } = gate();
    const getSession = vi.fn(async () => {
      await opened;
      return session;
    });
    const { provider, arrived } = setup(getSession);
    try {
      await provider.resolve(webview as never);
      provider.clearAccountCache();
      release();
      await settle();
      // The old answer must not be stored or announced: it may belong to an account that the user left.
      expect(arrived).not.toHaveBeenCalled();
      expect((await provider.resolve(webview as never)).user).toBeUndefined();
      expect(getSession).toHaveBeenCalledTimes(2);
      await settle();
      expect(arrived).toHaveBeenCalledTimes(1);
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });

  it("drops the answer of a lookup that ends after dispose", async () => {
    mockConfig.values = { accountAvatar: "github" };
    const { opened, release } = gate();
    const { provider, arrived } = setup(async () => {
      await opened;
      return session;
    });
    try {
      await provider.resolve(webview as never);
      provider.dispose();
      release();
      await settle();
      expect(arrived).not.toHaveBeenCalled();
    } finally {
      mockConfig.values = {};
    }
  });

  it("does not look up the account when an image of the user's own is set", async () => {
    const own = "data:image/png;base64,iVBORw0KGgo=";
    mockConfig.values = { accountAvatar: "github", userAvatar: own };
    const getSession = vi.fn(async () => session);
    const { provider } = setup(getSession);
    try {
      expect((await provider.resolve(webview as never)).user).toBe(own);
      await settle();
      expect(getSession).not.toHaveBeenCalled();
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });

  it("never shows the picture of the old account for a new choice", async () => {
    mockConfig.values = { accountAvatar: "github" };
    const asked: string[] = [];
    const { provider } = setup(async (providerId) => {
      asked.push(providerId);
      return session;
    });
    try {
      await provider.resolve(webview as never);
      await settle();
      expect((await provider.resolve(webview as never)).user).toBe(pngUri);

      mockConfig.values = { accountAvatar: "microsoft" };
      expect((await provider.resolve(webview as never)).user).toBeUndefined();
      await settle();
      expect(asked).toEqual(["github", "microsoft"]);
    } finally {
      mockConfig.values = {};
      provider.dispose();
    }
  });
});