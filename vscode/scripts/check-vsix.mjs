// Opens a VSIX and checks what the packaging promises (docs/specs/2026-11-04-vscode-extension-design.md, section 11).
//   node scripts/check-vsix.mjs out/zycode-linux-x64-*.vsix
import { readFileSync } from "node:fs";
import path from "node:path";

import { loadAssets, loadManifest, readZip, sha256Buffer } from "./lib.mjs";

/** Returns a list of problems. Empty means the file is as promised. */
export function checkVsix(file, expectedTarget) {
  const assets = loadAssets();
  const manifest = loadManifest();
  const entries = readZip(readFileSync(file));
  const byName = new Map(entries.map((e) => [e.name, e]));
  const problems = [];
  const need = (name) => {
    const entry = byName.get(name);
    if (!entry) problems.push(`missing: ${name}`);
    return entry;
  };

  const target = assets.targets[expectedTarget];
  if (!target) return [`unknown target ${expectedTarget}`];
  const program = need(`extension/bin/${target.program}`);
  if (program) {
    if (!expectedTarget.startsWith("win32") && ((program.mode ?? 0) & 0o111) !== 0o111) {
      problems.push(`extension/bin/${target.program} has no exec bit (mode ${(program.mode ?? 0).toString(8)})`);
    }
    if (program.size < 10 * 1024 * 1024) problems.push(`extension/bin/${target.program} is only ${program.size} bytes`);
  }
  for (const other of ["zycode-agent", "zycode-agent.exe"]) {
    if (other !== target.program && byName.has(`extension/bin/${other}`)) problems.push(`extension/bin/${other} is in the wrong platform package`);
  }
  for (const name of ["extension/LICENSE.txt", "extension/THIRD-PARTY-NOTICES", "extension/readme.md", "extension/changelog.md", "extension/package.json", "extension/dist/extension.js", "extension/dist/webview.js", "extension/media/chat.css", "extension/media/icon.png", "extension/media/zycode.svg"]) {
    need(name);
  }
  for (const walkthrough of manifest.contributes?.walkthroughs ?? []) {
    for (const step of walkthrough.steps ?? []) {
      const media = step.media?.markdown ?? step.media?.image;
      if (typeof media === "string") need(`extension/${media}`);
    }
  }
  const png = byName.get("extension/media/icon.png");
  if (png) {
    const data = png.read();
    const isPng = data.subarray(1, 4).toString("latin1") === "PNG";
    const width = data.readUInt32BE(16);
    const height = data.readUInt32BE(20);
    if (!isPng || width !== 256 || height !== 256) problems.push(`media/icon.png is ${width}x${height}, expected a 256x256 PNG`);
  }
  for (const entry of entries) {
    const name = entry.name;
    if (/(^|\/)node_modules\//.test(name)) problems.push(`node_modules in the package: ${name}`);
    if (/\.map$/.test(name)) problems.push(`source map in the package: ${name}`);
    if (/(^|\/)\.env/.test(name)) problems.push(`.env file in the package: ${name}`);
    if (/^extension\/(src|test|scripts)\//.test(name)) problems.push(`source in the package: ${name}`);
    if (/\.vsix$/.test(name) || /(^|\/)\.vsix-cache\//.test(name)) problems.push(`cache or package inside the package: ${name}`);
  }

  const vsixManifest = byName.get("extension.vsixmanifest")?.read().toString("utf8") ?? "";
  if (!vsixManifest) problems.push("missing: extension.vsixmanifest");
  if (!new RegExp(`TargetPlatform="${expectedTarget}"`).test(vsixManifest)) problems.push(`the manifest has no TargetPlatform="${expectedTarget}"`);
  if (/Microsoft\.VisualStudio\.Code\.PreRelease"\s+Value="true"/.test(vsixManifest)) problems.push("the manifest has the pre-release flag: a release must not have it");
  if (!new RegExp(`Identity[^>]*Id="${manifest.name}"[^>]*Version="${manifest.version.replace(/\./g, "\\.")}"[^>]*Publisher="${manifest.publisher}"`).test(vsixManifest)) {
    problems.push(`the manifest identity is not ${manifest.publisher}.${manifest.name} ${manifest.version}`);
  }
  const packed = byName.get("extension/package.json");
  if (packed) {
    const inner = JSON.parse(packed.read().toString("utf8"));
    if (inner.zycodeAgentVersion !== assets.version) problems.push(`package.json zycodeAgentVersion ${inner.zycodeAgentVersion} != pin ${assets.version}`);
  }
  return problems;
}

export function describeVsix(file) {
  const buffer = readFileSync(file);
  return { path: file, bytes: buffer.length, sha256: sha256Buffer(buffer) };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const file = process.argv[2];
  const target = process.argv[3] ?? /zycode-([a-z0-9]+-[a-z0-9]+)-/.exec(path.basename(file ?? ""))?.[1];
  if (!file || !target) {
    console.error("usage: node scripts/check-vsix.mjs <file.vsix> [target]");
    process.exit(2);
  }
  const problems = checkVsix(file, target);
  if (problems.length > 0) {
    console.error(`${file}:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`${file}: ok`);
}
