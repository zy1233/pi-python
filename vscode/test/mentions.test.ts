import { describe, expect, it } from "vitest";

import {
  containsMentionToken,
  deriveFolders,
  formatMentionToken,
  formatPromptWithMentions,
  fuzzyScore,
  isLikelySecretPath,
  mentionDetail,
  mentionKey,
  mentionLabel,
  normalizeRelativePath,
  rankMentions,
  removeMentionToken,
  type MentionCandidate,
} from "../src/core/mentions.js";

describe("normalizeRelativePath", () => {
  it("normalizes Windows separators, leading ./, and folder trailing slashes", () => {
    expect(normalizeRelativePath(".\\src\\core\\chat-controller.ts", "file")).toBe("src/core/chat-controller.ts");
    expect(normalizeRelativePath("./src//app.ts/", "file")).toBe("src/app.ts");
    expect(normalizeRelativePath("src\\core", "folder")).toBe("src/core/");
    expect(normalizeRelativePath("src/core/", "folder")).toBe("src/core/");
    expect(normalizeRelativePath("README.md", "file")).toBe("README.md");
  });

  it("rejects empty paths, traversal, absolute paths, control chars, and oversized paths", () => {
    const bad = [
      "",
      "   ",
      ".",
      "..",
      "../secret.txt",
      "src/../secret.txt",
      "/etc/passwd",
      "\\Windows\\System32",
      "C:\\work\\file.ts",
      "d:/work/file.ts",
      "src/\u0000file.ts",
      "src/\nfile.ts",
      "a".repeat(513),
    ];
    for (const raw of bad) {
      expect(normalizeRelativePath(raw, "file"), raw).toBeUndefined();
      expect(normalizeRelativePath(raw, "folder"), raw).toBeUndefined();
    }
  });
});

describe("deriveFolders", () => {
  it("extracts unique sorted parent folders with trailing slashes", () => {
    expect(deriveFolders(["README.md", "src/core/a.ts", "src/core/b.ts", "src/ui/view.ts"])).toEqual([
      "src/",
      "src/core/",
      "src/ui/",
    ]);
  });
});

describe("fuzzyScore", () => {
  it("matches substrings and boundary-aligned subsequences with smart-case", () => {
    const sub = fuzzyScore("chat", "chat-controller.ts");
    expect(sub?.indices).toEqual([0, 1, 2, 3]);

    const boundary = fuzzyScore("cc", "chat-controller.ts");
    expect(boundary?.indices).toEqual([0, 5]);

    const camel = fuzzyScore("cv", "chatView.ts");
    expect(camel?.indices).toEqual([0, 4]);

    // All-lowercase query is case-insensitive; uppercase query requires exact case.
    expect(fuzzyScore("readme", "README.md")).toBeDefined();
    expect(fuzzyScore("Readme", "README.md")).toBeUndefined();
    expect(fuzzyScore("README", "README.md")).toBeDefined();
    expect(fuzzyScore("zzz", "src/app.ts")).toBeUndefined();
  });
});

describe("rankMentions", () => {
  const candidates: MentionCandidate[] = [
    { kind: "file", path: "src/core/chat-controller.ts", label: "chat-controller.ts", detail: "src/core" },
    { kind: "file", path: "src/core/other.ts", label: "other.ts", detail: "src/core" },
    { kind: "file", path: "README.md", label: "README.md", detail: "" },
    { kind: "folder", path: "src/", label: "src/", detail: "Folder", badge: "folder" },
    { kind: "folder", path: "src/core/", label: "src/core/", detail: "Folder", badge: "folder" },
    { kind: "file", path: "src/open.ts", label: "open.ts", detail: "src", badge: "open" },
    { kind: "file", path: "src/active.ts", label: "active.ts", detail: "src", badge: "active" },
    {
      kind: "selection",
      path: "src/active.ts",
      range: { startLine: 10, endLine: 20 },
      label: "active.ts:L10-L20",
      detail: "src/active.ts",
      badge: "selection",
    },
  ];

  it("orders empty query as selection > active > open > shallow folders/files", () => {
    const ranked = rankMentions(candidates, "", 10);
    expect(ranked.map((r) => `${r.badge ?? "file"}:${r.path}`)).toEqual([
      "selection:src/active.ts",
      "active:src/active.ts",
      "open:src/open.ts",
      "folder:src/",
      "folder:src/core/",
      "file:README.md",
      "file:src/core/chat-controller.ts",
      "file:src/core/other.ts",
    ]);
  });

  it("respects limit and prioritizes folder drill-down when query ends with /", () => {
    const drilled = rankMentions(candidates, "src/core/", 2);
    expect(drilled).toHaveLength(2);
    expect(drilled[0]?.path).toBe("src/core/");

    const inside = rankMentions(candidates, "src/core/", 10);
    expect(inside.slice(0, 3).map((i) => i.path)).toContain("src/core/chat-controller.ts");
  });

  it("computes labelMatches and detailMatches cleanly", () => {
    const byLabel = rankMentions(candidates, "chat", 5);
    expect(byLabel[0]?.path).toBe("src/core/chat-controller.ts");
    expect(byLabel[0]?.labelMatches).toEqual([0, 1, 2, 3]);

    const byPath = rankMentions(candidates, "core/other", 5);
    expect(byPath[0]?.path).toBe("src/core/other.ts");
    expect(byPath[0]?.detailMatches).toEqual([4, 5, 6, 7]);
    expect(byPath[0]?.labelMatches).toEqual([0, 1, 2, 3, 4]);
  });
});

describe("mention formatting and prompt assembly", () => {
  it("formats labels, details, keys, and tokens including spaces and line ranges", () => {
    const file = { kind: "file" as const, path: "src/app.ts" };
    const spaced = { kind: "file" as const, path: "my docs/note.md" };
    const singleLine = { kind: "selection" as const, path: "src/app.ts", range: { startLine: 5, endLine: 5 } };
    const multiLine = { kind: "selection" as const, path: "src/app.ts", range: { startLine: 5, endLine: 12 } };
    const folder = { kind: "folder" as const, path: "src/core/" };

    expect(mentionLabel(file)).toBe("app.ts");
    expect(mentionDetail(file)).toBe("src");
    expect(mentionLabel(folder)).toBe("src/core/");
    expect(mentionDetail(folder)).toBe("Folder");
    expect(mentionLabel(singleLine)).toBe("app.ts:L5");
    expect(mentionLabel(multiLine)).toBe("app.ts:L5-L12");
    expect(mentionKey(multiLine)).toBe("selection:src/app.ts:5-12");

    expect(formatMentionToken(file)).toBe("@src/app.ts");
    expect(formatMentionToken(spaced)).toBe('@"my docs/note.md"');
    expect(formatMentionToken(singleLine)).toBe("@src/app.ts:L5");
    expect(formatMentionToken(multiLine)).toBe("@src/app.ts:L5-L12");
    expect(formatMentionToken(folder)).toBe("@src/core/");
  });

  it("avoids duplicating inline tokens, prepends only missing attachments, and leaves /slash commands unprefixed", () => {
    const a = { kind: "file" as const, path: "src/a.ts" };
    const b = { kind: "file" as const, path: "src/b.ts" };

    expect(formatPromptWithMentions("Inspect @src/a.ts", [a, a])).toBe("Inspect @src/a.ts");
    expect(formatPromptWithMentions("Inspect @src/a.ts", [a, b])).toBe("Referenced context: @src/b.ts\n\nInspect @src/a.ts");
    expect(formatPromptWithMentions("Explain", [a, b])).toBe("Referenced context: @src/a.ts @src/b.ts\n\nExplain");
    expect(formatPromptWithMentions("/mcp", [a])).toBe("/mcp");
    expect(formatPromptWithMentions("  /goal inspect @src/a.ts", [a, b])).toBe("  /goal inspect @src/a.ts");
  });

  it("identifies secret-looking paths while excluding safe templates, public keys, CA certs, backups, and type defs", () => {
    for (const secretPath of [
      ".env",
      ".env.local",
      ".envrc",
      "config/prod.env",
      "certs/server.pem",
      "certs/private.key",
      "bundle.p12",
      "cert.pfx",
      "store.jks",
      "app.keystore",
      "putty.ppk",
      "prod.tfvars",
      "prod.tfstate",
      "app.secrets",
      "ssh.keys",
      "keys/id_rsa",
      "keys/id_ed25519",
      "keys/id_ecdsa.key",
      "keys/id_dsa",
      ".npmrc",
      ".pypirc",
      ".netrc",
      "credentials",
      "config/credentials.json",
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
      ".aws/credentials",
      "home/.aws/credentials",
      ".docker/config.json",
      "users/me/.docker/config.json",
    ]) {
      expect(isLikelySecretPath(secretPath), secretPath).toBe(true);
    }

    for (const safePath of [
      ".env.example",
      ".env.sample",
      ".env.template",
      ".env.dist",
      ".env.d.ts",
      "local.env.example",
      "keys/id_rsa.pub",
      "keys/id_ed25519.pub",
      "keys/id_rsa.bak",
      "ca.pem",
      "certs/ca.pem",
      "certs/cacert.pem",
      "certs/root-ca.pem",
      "config.json",
      "docs/secrets.md",
      "src/app.ts",
      "package.json",
    ]) {
      expect(isLikelySecretPath(safePath), safePath).toBe(false);
    }
  });

  it("removes mention tokens cleanly without eating unrelated tokens or newlines", () => {
    const target = { kind: "file" as const, path: "src/a.ts" };
    expect(removeMentionToken("@src/a.ts fix bug", target)).toBe("fix bug");
    expect(removeMentionToken("fix @src/a.ts bug", target)).toBe("fix bug");
    expect(removeMentionToken("fix bug @src/a.ts", target)).toBe("fix bug");
    expect(removeMentionToken("check @src/a.tsx and @src/a.ts", target)).toBe("check @src/a.tsx and");
    expect(removeMentionToken("line1\n@src/a.ts\nline2", target)).toBe("line1\n\nline2");
  });

  it("detects mention tokens strictly at token boundaries", () => {
    const file = { kind: "file" as const, path: "src/a.ts" };
    const spaced = { kind: "file" as const, path: "my docs/note.md" };
    const selection = { kind: "selection" as const, path: "src/a.ts", range: { startLine: 5, endLine: 12 } };

    expect(containsMentionToken("check @src/a.ts now", file)).toBe(true);
    expect(containsMentionToken("@src/a.ts", file)).toBe(true);
    expect(containsMentionToken("check @src/a.tsx", file)).toBe(false);
    expect(containsMentionToken("email@src/a.ts", file)).toBe(false);
    expect(containsMentionToken('open @"my docs/note.md" please', spaced)).toBe(true);
    expect(containsMentionToken("see @src/a.ts:L5-L12, please", selection)).toBe(true);
    expect(containsMentionToken("see @src/a.ts:L5-L120", selection)).toBe(false);
    expect(containsMentionToken("see @src/a.ts.", file)).toBe(true);
    expect(containsMentionToken("see @src/a.ts. Next sentence", file)).toBe(true);
    expect(containsMentionToken("see @src/a.ts- explain", file)).toBe(true);
    expect(containsMentionToken("see @src/a.ts:L5-L12.", selection)).toBe(true);
    expect(containsMentionToken("see @src/a.ts.bak", file)).toBe(false);
    expect(containsMentionToken("see @src/a.ts-old", file)).toBe(false);
    expect(removeMentionToken("see @src/a.ts.", file)).toBe("see.");
    expect(removeMentionToken("see @src/a.ts. Next", file)).toBe("see. Next");
  });
});
