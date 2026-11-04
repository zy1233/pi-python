// Downloads the agent Release asset of one target, checks it against agent-assets.json, and puts
// the program in bin/. The pin is the file in this repository: a different sha256 on the Release is
// a failure, not an update.
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

import { CACHE_DIR, ROOT, loadAssets, readSingleTarGzFile, readZip, sha256File } from "./lib.mjs";

export function assetUrl(assets, target) {
  const entry = assets.targets[target];
  return `https://github.com/${assets.repository}/releases/download/${assets.tag}/${entry.asset}`;
}

async function download(url, destination) {
  mkdirSync(path.dirname(destination), { recursive: true });
  const partial = `${destination}.part`;
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const response = await fetch(url, { redirect: "follow" });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      writeFileSync(partial, Buffer.from(await response.arrayBuffer()));
      renameSync(partial, destination);
      return;
    } catch (error) {
      if (attempt === 4) throw new Error(`download of ${url} failed: ${error}`);
      await new Promise((resolve) => setTimeout(resolve, 2000 * 2 ** (attempt - 1)));
    }
  }
}

/** Returns the path of the verified archive in the cache. */
export async function fetchArchive(target) {
  const assets = loadAssets();
  const entry = assets.targets[target];
  if (!entry) throw new Error(`no agent asset is pinned for target ${target}`);
  const archive = path.join(CACHE_DIR, assets.tag, entry.asset);
  const verified = async () => existsSync(archive) && statSync(archive).size === entry.size && (await sha256File(archive)) === entry.sha256;
  if (!(await verified())) {
    console.log(`downloading ${assetUrl(assets, target)}`);
    await download(assetUrl(assets, target), archive);
    if (statSync(archive).size !== entry.size) {
      rmSync(archive, { force: true });
      throw new Error(`${entry.asset}: size is not the pinned ${entry.size}`);
    }
    const actual = await sha256File(archive);
    if (actual !== entry.sha256) {
      rmSync(archive, { force: true });
      throw new Error(`${entry.asset}: sha256 ${actual} is not the pinned ${entry.sha256}`);
    }
  }
  return archive;
}

/** Puts the program of `target` in `<ROOT>/bin`, with mode 0755 on POSIX targets. */
export async function installAgent(target) {
  const assets = loadAssets();
  const entry = assets.targets[target];
  const archive = await fetchArchive(target);
  const buffer = readFileSync(archive);
  let data;
  if (entry.asset.endsWith(".tar.gz")) {
    const file = readSingleTarGzFile(buffer);
    if (file.name.replace(/^\.\//, "") !== entry.program) throw new Error(`unexpected file ${file.name} in ${entry.asset}`);
    data = file.data;
  } else {
    const files = readZip(buffer).filter((e) => !e.name.endsWith("/"));
    if (files.length !== 1 || files[0].name !== entry.program) {
      throw new Error(`unexpected content in ${entry.asset}: ${files.map((f) => f.name).join(", ")}`);
    }
    data = files[0].read();
  }
  const bin = path.join(ROOT, "bin");
  rmSync(bin, { recursive: true, force: true });
  mkdirSync(bin, { recursive: true });
  const program = path.join(bin, entry.program);
  writeFileSync(program, data);
  if (!target.startsWith("win32")) chmodSync(program, 0o755);
  return program;
}
