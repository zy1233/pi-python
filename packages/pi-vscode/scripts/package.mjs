// Makes the VSIX of one target, or of all five.
//   node scripts/package.mjs linux-x64
//   node scripts/package.mjs all
// See docs/specs/2026-11-04-vscode-extension-design.md, section 11. Run on Linux: the exec bit of the
// binary survives in the VSIX only when the package is made there.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { checkVsix, describeVsix } from "./check-vsix.mjs";
import { installAgent } from "./fetch-agent.mjs";
import { OUT_DIR, REPO_ROOT, ROOT, TARGETS, formatSize, loadAssets, loadManifest } from "./lib.mjs";
import { buildNotices } from "./notices.mjs";

function run(command, args) {
  console.log(`+ ${command} ${args.join(" ")}`);
  execFileSync(command, args, { cwd: ROOT, stdio: "inherit", shell: false });
}

function vsceCommand() {
  const bin = path.join(ROOT, "node_modules", "@vscode", "vsce", "vsce");
  if (!existsSync(bin)) throw new Error("@vscode/vsce is not installed: run `npm ci` first");
  return [process.execPath, bin];
}

export async function packageTarget(target, { prebuilt = false } = {}) {
  const manifest = loadManifest();
  const assets = loadAssets();
  if (!assets.targets[target]) throw new Error(`unknown target ${target} (known: ${TARGETS.join(", ")})`);
  await installAgent(target);
  if (!prebuilt) run(process.execPath, ["esbuild.mjs", "--production", "--metafile"]);
  copyFileSync(path.join(REPO_ROOT, "LICENSE"), path.join(ROOT, "LICENSE"));
  writeFileSync(path.join(ROOT, "THIRD-PARTY-NOTICES"), buildNotices().text);
  mkdirSync(OUT_DIR, { recursive: true });
  const out = path.join(OUT_DIR, `zycode-${target}-${manifest.version}.vsix`);
  rmSync(out, { force: true });
  const [node, vsce] = vsceCommand();
  run(node, [vsce, "package", "--target", target, "--no-dependencies", "--out", out]);
  const problems = checkVsix(out, target);
  if (problems.length > 0) throw new Error(`${out} is not as promised:\n  ${problems.join("\n  ")}`);
  return describeVsix(out);
}

async function main() {
  if (process.platform !== "linux") {
    console.error("Package on Linux (or WSL): a VSIX made on Windows has no exec bit on the binary.");
    process.exit(1);
  }
  const arg = process.argv[2];
  if (!arg) {
    console.error(`usage: node scripts/package.mjs <${TARGETS.join("|")}|all>`);
    process.exit(2);
  }
  const targets = arg === "all" ? TARGETS : [arg];
  const results = [];
  for (const [index, target] of targets.entries()) {
    results.push({ target, ...(await packageTarget(target, { prebuilt: index > 0 })) });
  }
  writeFileSync(path.join(OUT_DIR, "SHA256SUMS"), mergeSums(results));
  console.log("\nFiles for a hand upload:");
  for (const r of results) console.log(`  ${r.path}\n    ${formatSize(r.bytes)}  sha256 ${r.sha256}`);
}

/** SHA256SUMS keeps the lines of earlier runs for other targets, so `package linux-x64` does not drop the rest. */
function mergeSums(results) {
  const file = path.join(OUT_DIR, "SHA256SUMS");
  const lines = new Map();
  const add = (line) => {
    const match = /^([0-9a-f]{64}) {2}(.+)$/.exec(line);
    if (match) lines.set(match[2], line);
  };
  if (existsSync(file)) readFileSync(file, "utf8").split("\n").forEach(add);
  for (const r of results) add(`${r.sha256}  ${path.basename(r.path)}`);
  for (const name of [...lines.keys()]) if (!existsSync(path.join(OUT_DIR, name))) lines.delete(name);
  return [...lines.values()].sort().join("\n") + "\n";
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
}
