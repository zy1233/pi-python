import { readFileSync } from "node:fs";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";

import { TARGETS, loadAssets, readSingleTarGzFile, readZip } from "../scripts/lib.mjs";

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
