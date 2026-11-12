import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";

import { TARGETS, loadAssets, readSingleTarGzFile, readZip } from "../scripts/lib.mjs";
import { bundledPackages } from "../scripts/notices.mjs";

function tarEntry(name: string, type: string, data: Buffer, mode = 0o755): Buffer {
  const header = Buffer.alloc(512);
  header.write(name, 0, "utf8");
  header.write(mode.toString(8).padStart(7, "0"), 100, "ascii");
  header.write(data.length.toString(8).padStart(11, "0"), 124, "ascii");
  header.write(type, 156, "ascii");
  const padded = Buffer.alloc(Math.ceil(data.length / 512) * 512);
  data.copy(padded);
  return Buffer.concat([header, padded]);
}

function tarGz(...entries: Buffer[]): Buffer {
  return gzipSync(Buffer.concat([...entries, Buffer.alloc(1024)]));
}

function storedZip(name: string, data: Buffer, unixMode: number): Buffer {
  const nameBytes = Buffer.from(name);
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt32LE(data.length, 18);
  local.writeUInt32LE(data.length, 22);
  local.writeUInt16LE(nameBytes.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE((3 << 8) | 20, 4);
  central.writeUInt32LE(data.length, 20);
  central.writeUInt32LE(data.length, 24);
  central.writeUInt16LE(nameBytes.length, 28);
  central.writeUInt32LE((unixMode << 16) >>> 0, 38);
  const centralOffset = local.length + nameBytes.length + data.length;
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(1, 8);
  eocd.writeUInt16LE(1, 10);
  eocd.writeUInt32LE(central.length + nameBytes.length, 12);
  eocd.writeUInt32LE(centralOffset, 16);
  return Buffer.concat([local, nameBytes, data, central, nameBytes, eocd]);
}

describe("agent-assets.json", () => {
  const assets = loadAssets();

  it("pins one archive with a sha256 for each of the five targets", () => {
    expect(Object.keys(assets.targets).sort()).toEqual([...TARGETS].sort());
    for (const [target, entry] of Object.entries(assets.targets)) {
      expect(entry.sha256, target).toMatch(/^[0-9a-f]{64}$/);
      expect(entry.size, target).toBeGreaterThan(1_000_000);
      expect(entry.asset, target).toContain(assets.version);
      expect(entry.program, target).toBe(target.startsWith("win32") ? "zycode-agent.exe" : "zycode-agent");
    }
  });

  it("names the same agent version in the Python pin and the tag", () => {
    expect(assets.tag).toBe(`v${assets.version}`);
    expect(assets.pythonPackages).toContain(`pi-agent-cli-lc==${assets.version}`);
  });

  it("is the version that package.json states", () => {
    const manifest = JSON.parse(readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
    expect(manifest.zycodeAgentVersion).toBe(assets.version);
  });
});

describe("archive readers", () => {
  it("reads the one file of a tar.gz and skips PAX headers and folders", () => {
    const archive = tarGz(
      tarEntry("././@PaxHeader", "x", Buffer.from("30 mtime=1\n")),
      tarEntry("bin/", "5", Buffer.alloc(0)),
      tarEntry("zycode-agent", "0", Buffer.from("program"), 0o755),
    );
    const file = readSingleTarGzFile(archive);
    expect(file.name).toBe("zycode-agent");
    expect(file.mode & 0o111).not.toBe(0);
    expect(file.data.toString()).toBe("program");
  });

  it("refuses a tar.gz with a link, or with two files", () => {
    expect(() => readSingleTarGzFile(tarGz(tarEntry("a", "2", Buffer.alloc(0))))).toThrow(/entry type/);
    expect(() =>
      readSingleTarGzFile(tarGz(tarEntry("a", "0", Buffer.from("1")), tarEntry("b", "0", Buffer.from("2")))),
    ).toThrow(/one file/);
  });

  it("reads a stored zip entry with its Unix mode", () => {
    const entries = readZip(storedZip("zycode-agent.exe", Buffer.from("MZ"), 0o100755));
    expect(entries).toHaveLength(1);
    expect(entries[0]?.name).toBe("zycode-agent.exe");
    expect((entries[0]?.mode ?? 0) & 0o111).not.toBe(0);
    expect(entries[0]?.read().toString()).toBe("MZ");
  });

  it("refuses data that is not a zip file", () => {
    expect(() => readZip(Buffer.from("not a zip file at all, just text"))).toThrow(/not a zip/);
  });
});

describe("bundledPackages (the npm part of THIRD-PARTY-NOTICES)", () => {
  const byDir = (inputs: string[]) => Object.fromEntries(bundledPackages(inputs).map((p) => [p.dir, p.name]));

  it("names a package from the folder under the last node_modules, so a nested package is not lost", () => {
    expect(
      byDir([
        "node_modules/mdurl/lib/index.mjs",
        "node_modules/@scope/pkg/dist/a.js",
        "node_modules/markdown-it/lib/index.mjs",
        "node_modules/markdown-it/node_modules/entities/dist/decode.js",
        "src/webview/main.ts",
      ]),
    ).toEqual({
      "node_modules/mdurl": "mdurl",
      "node_modules/@scope/pkg": "@scope/pkg",
      "node_modules/markdown-it": "markdown-it",
      "node_modules/markdown-it/node_modules/entities": "entities",
    });
  });

  it("lists two versions of one name, one entry for each folder, and one entry for a folder that many files name", () => {
    const found = bundledPackages([
      "node_modules/entities/lib/a.js",
      "node_modules/entities/lib/b.js",
      "node_modules/markdown-it/node_modules/entities/dist/a.js",
      "node_modules/markdown-it/node_modules/entities/dist/b.js",
    ]);
    expect(found).toEqual([
      { name: "entities", dir: "node_modules/entities" },
      { name: "entities", dir: "node_modules/markdown-it/node_modules/entities" },
    ]);
  });

  it("finds a package above the project folder, and skips files that are not in a package", () => {
    expect(byDir(["../../node_modules/foo/index.js", "node_modules/loose.js", "dist/x.js"])).toEqual({
      "../../node_modules/foo": "foo",
    });
  });
});

describe("release files", () => {
  const root = path.join(__dirname, "..");
  const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
  const workflow = readFileSync(path.join(root, "..", "..", ".github", "workflows", "vscode-extension.yml"), "utf8");

  it("is the extension zycode.zycode that the registry entries name", () => {
    expect(manifest.publisher).toBe("zycode");
    expect(manifest.name).toBe("zycode");
  });

  it("has a CHANGELOG entry for the version in package.json", () => {
    const changelog = readFileSync(path.join(root, "CHANGELOG.md"), "utf8");
    expect(changelog).toMatch(new RegExp(`^## ${manifest.version.replace(/\./g, "\\.")}( |$)`, "m"));
  });

  it("publishes with Trusted Publishing from the environment, and stores no token", () => {
    expect(workflow).toContain("environment: vscode-publish");
    expect(workflow.match(/id-token: write/g)).toHaveLength(1);
    expect(workflow).toContain("ovsx publish --trusted-publishing");
    expect(workflow).toContain("vsce publish --oidc");
    expect(workflow).not.toMatch(/secrets\./);
    expect(workflow).not.toMatch(/OVSX_PAT|VSCE_PAT|--pat\b|-p "/);
  });

  it("is a stable release: no pre-release flag in the scripts or the workflow, and an even minor", () => {
    expect(manifest.version).toMatch(/^\d+\.\d+\.\d+$/);
    expect(Number(manifest.version.split(".")[1]) % 2).toBe(0);
    expect(workflow).not.toMatch(/pre-release/i);
    for (const script of ["package.mjs", "check-vsix.mjs"]) {
      const text = readFileSync(path.join(root, "scripts", script), "utf8");
      expect(text).not.toContain('"--pre-release"');
    }
    expect(readFileSync(path.join(root, "scripts", "check-vsix.mjs"), "utf8")).toMatch(/must not have it/);
  });

  it("is above the pre-release 0.1.0 that is published", () => {
    const [major = 0, minor = 0] = manifest.version.split(".").map(Number);
    expect(major * 1000 + minor).toBeGreaterThan(0 * 1000 + 1);
  });

  it("names a CHANGELOG entry without the word pre-release", () => {
    const changelog = readFileSync(path.join(root, "CHANGELOG.md"), "utf8");
    const entry = new RegExp(`^## ${manifest.version.replace(/\./g, "\\.")}.*$`, "m").exec(changelog)?.[0] ?? "";
    expect(entry).not.toMatch(/pre-release/i);
  });

  it("starts a publish by hand only, from main", () => {
    expect(workflow).toContain("workflow_dispatch:");
    expect(workflow).not.toMatch(/^\s*(workflow_run|release|push|schedule):/m);
    expect(workflow).not.toMatch(/tags:/);
    expect(workflow).toContain("github.ref != 'refs/heads/main'");
  });
});

describe("manifest contributions", () => {
  const root = path.join(__dirname, "..");
  const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
  const ignore = readFileSync(path.join(root, ".vscodeignore"), "utf8");

  it("declares every command that a menu or the guide uses", () => {
    const declared = new Set<string>(manifest.contributes.commands.map((c: { command: string }) => c.command));
    const used: string[] = [];
    for (const items of Object.values(manifest.contributes.menus) as { command: string }[][]) for (const item of items) used.push(item.command);
    for (const walkthrough of manifest.contributes.walkthroughs) {
      for (const step of walkthrough.steps) for (const m of step.description.matchAll(/command:(zycode\.[A-Za-z.]+)/g)) used.push(m[1]!);
    }
    for (const command of used) {
      if (command === "zycode.sessions.focus") continue;
      expect(declared.has(command), command).toBe(true);
    }
  });

  it("has a walkthrough whose pages are in the package", () => {
    const [walkthrough] = manifest.contributes.walkthroughs;
    expect(walkthrough.id).toBe("zycode.gettingStarted");
    expect(walkthrough.steps.length).toBeGreaterThanOrEqual(5);
    expect(ignore).toContain("!media/walkthrough/**");
    for (const step of walkthrough.steps) {
      const file = path.join(root, step.media.markdown);
      expect(existsSync(file), step.media.markdown).toBe(true);
    }
  });

  it("has the view of past chats in the zycode container", () => {
    expect(manifest.contributes.views.zycode.map((v: { id: string }) => v.id)).toEqual(["zycode.chat", "zycode.sessions"]);
    expect(manifest.activationEvents).toContain("onView:zycode.sessions");
  });

  it("offers ask and auto in the setting, never always-approve", () => {
    const setting = manifest.contributes.configuration.properties["zycode.permissionMode"];
    expect(setting.enum).toEqual(["ask", "auto"]);
    expect(setting.default).toBe("ask");
  });

  it("does not use any walkthrough text that asks for a key in the chat", () => {
    const text = readdirSync(path.join(root, "media", "walkthrough")).map((f) => readFileSync(path.join(root, "media", "walkthrough", f), "utf8")).join("\n");
    expect(text).toMatch(/Never type it in the chat/);
  });
});
