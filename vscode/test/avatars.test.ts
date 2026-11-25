import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("vscode", () => ({
  authentication: { getSession: vi.fn(async () => undefined) },
  workspace: { getConfiguration: () => ({ get: (_k: string, d?: unknown) => d }) },
  Uri: { joinPath: (base: { fsPath: string }, ...segs: string[]) => ({ fsPath: path.join(base.fsPath, ...segs), toString: () => "" }) },
}));

import { resolveCustomAvatar, resolveVscodeAccountAvatar } from "../src/ui/avatars.js";

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

describe("resolveVscodeAccountAvatar", () => {
  it("silently returns undefined when no account session exists and never sets createIfNone", async () => {
    const calls: { providerId: string; options: { silent: true } }[] = [];
    const out = await resolveVscodeAccountAvatar({
      getSession: async (providerId, _scopes, options) => {
        calls.push({ providerId, options });
        return undefined;
      },
    });
    expect(out).toBeUndefined();
    expect(calls.length).toBeGreaterThan(0);
    for (const c of calls) expect(c.options).toEqual({ silent: true });
  });

  it("resolves GitHub avatar via /user API and falls back to avatars.githubusercontent.com/u/<id>", async () => {
    const imgBytes = new Uint8Array([10, 20, 30]);
    const expected = `data:image/png;base64,${Buffer.from(imgBytes).toString("base64")}`;

    const viaApi = await resolveVscodeAccountAvatar({
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
    const viaIdFallback = await resolveVscodeAccountAvatar({
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

  it("resolves Microsoft Graph photo when GitHub is not signed in", async () => {
    const jpgBytes = new Uint8Array([255, 216, 255]);
    const out = await resolveVscodeAccountAvatar({
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
