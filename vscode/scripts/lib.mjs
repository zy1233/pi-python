// Helpers of the packaging scripts. Node 20+, no dependencies.
import { createHash } from "node:crypto";
import { createReadStream, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync, inflateRawSync } from "node:zlib";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_ROOT = path.resolve(ROOT, "..");
export const CACHE_DIR = path.join(ROOT, ".vsix-cache");
export const OUT_DIR = path.join(ROOT, "out");

export const TARGETS = ["linux-x64", "linux-arm64", "darwin-arm64", "darwin-x64", "win32-x64"];

export function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

export function loadManifest() {
  return readJson(path.join(ROOT, "package.json"));
}

export function loadAssets() {
  const assets = readJson(path.join(ROOT, "agent-assets.json"));
  const manifest = loadManifest();
  if (assets.version !== manifest.zycodeAgentVersion) {
    throw new Error(
      `agent-assets.json is for agent ${assets.version}, package.json zycodeAgentVersion is ${manifest.zycodeAgentVersion}`,
    );
  }
  return assets;
}

export function sha256Buffer(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

export function sha256File(file) {
  return new Promise((resolve, reject) => {
    const hash = createHash("sha256");
    createReadStream(file)
      .on("data", (chunk) => hash.update(chunk))
      .on("error", reject)
      .on("end", () => resolve(hash.digest("hex")));
  });
}

/** The one regular file of a `.tar.gz`: `{ name, mode, data }`. Fails for any other layout. */
export function readSingleTarGzFile(buffer) {
  const tar = gunzipSync(buffer);
  const files = [];
  let offset = 0;
  while (offset + 512 <= tar.length) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every((byte) => byte === 0)) break;
    const name = header.subarray(0, 100).toString("utf8").replace(/\0.*$/, "");
    const sizeText = header.subarray(124, 136).toString("utf8").replace(/\0.*$/, "").trim();
    const mode = parseInt(header.subarray(100, 108).toString("utf8").replace(/\0.*$/, "").trim() || "0", 8);
    const type = String.fromCharCode(header[156] || 48);
    const size = parseInt(sizeText || "0", 8);
    offset += 512;
    if (type === "0" || type === "\0") {
      files.push({ name, mode, data: tar.subarray(offset, offset + size) });
    } else if (type !== "5" && type !== "x" && type !== "g") {
      throw new Error(`unexpected tar entry type ${JSON.stringify(type)} for ${name}`);
    }
    offset += Math.ceil(size / 512) * 512;
  }
  if (files.length !== 1) throw new Error(`expected one file in the archive, found ${files.length}`);
  return files[0];
}

/** Entries of a zip file: `{ name, mode, method, compressedSize, size, read() }`. */
export function readZip(buffer) {
  let eocd = -1;
  for (let i = buffer.length - 22; i >= Math.max(0, buffer.length - 65557); i--) {
    if (buffer.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("not a zip file (no end of central directory)");
  const count = buffer.readUInt16LE(eocd + 10);
  let offset = buffer.readUInt32LE(eocd + 16);
  const entries = [];
  for (let n = 0; n < count; n++) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error("bad central directory entry");
    const versionMadeBy = buffer.readUInt16LE(offset + 4);
    const method = buffer.readUInt16LE(offset + 10);
    const compressedSize = buffer.readUInt32LE(offset + 20);
    const size = buffer.readUInt32LE(offset + 24);
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const externalAttrs = buffer.readUInt32LE(offset + 38);
    const localOffset = buffer.readUInt32LE(offset + 42);
    const name = buffer.subarray(offset + 46, offset + 46 + nameLength).toString("utf8");
    const unixMode = versionMadeBy >> 8 === 3 ? externalAttrs >>> 16 : undefined;
    entries.push({
      name,
      mode: unixMode,
      method,
      compressedSize,
      size,
      read() {
        const nameLen = buffer.readUInt16LE(localOffset + 26);
        const extraLen = buffer.readUInt16LE(localOffset + 28);
        const start = localOffset + 30 + nameLen + extraLen;
        const raw = buffer.subarray(start, start + compressedSize);
        if (method === 0) return Buffer.from(raw);
        if (method === 8) return inflateRawSync(raw);
        throw new Error(`zip method ${method} is not supported (${name})`);
      },
    });
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

export function formatSize(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MiB` : `${(bytes / 1024).toFixed(1)} KiB`;
}
