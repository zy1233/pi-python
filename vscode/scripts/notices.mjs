// Writes THIRD-PARTY-NOTICES: part 1 is the Python distributions in the agent binary, part 2 the
// npm packages that esbuild bundled into dist/. See docs/specs/2026-11-04-vscode-extension-design.md, section 11.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { CACHE_DIR, ROOT, loadAssets, readJson } from "./lib.mjs";

// Distributions whose license text is not in their metadata. Each one was read by a person.
// name (lower case) -> reviewed license. A distribution that is "UNKNOWN" and not here fails the build.
const REVIEWED_LICENSES = {};

const CPYTHON_TEXT = `CPython (the Python interpreter and standard library inside the binary)
License: PSF-2.0 (Python Software Foundation License Version 2)
https://docs.python.org/3/license.html
The interpreter links native libraries whose set depends on the platform build, among them OpenSSL
(Apache-2.0), zlib (Zlib), libffi (MIT), bzip2 (bzip2-1.0.6), liblzma / xz (0BSD / public domain),
SQLite (public domain), expat (MIT), mpdecimal (BSD-2-Clause) and, on Windows, the Microsoft Visual
C++ runtime (redistributable under its own terms).`;

const PYINSTALLER_TEXT = `PyInstaller bootloader (the small program at the start of the binary)
License: GPL-2.0-or-later WITH Bootloader-exception
https://pyinstaller.org/en/stable/license.html
The bootloader exception lets a program that PyInstaller builds be distributed under any terms.
PyInstaller's other parts are not in the binary.`;

function venvPython(venv) {
  return process.platform === "win32" ? path.join(venv, "Scripts", "python.exe") : path.join(venv, "bin", "python");
}

function hasCommand(command) {
  try {
    execFileSync(command, ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

/** A clean virtual environment with the packages of the agent build. Uses `uv` if it is there, else `python -m venv` and pip. */
function pythonDistributions(assets) {
  const venv = path.join(CACHE_DIR, "notices-venv");
  const python = venvPython(venv);
  const useUv = hasCommand("uv");
  if (!existsSync(python)) {
    mkdirSync(CACHE_DIR, { recursive: true });
    if (useUv) execFileSync("uv", ["venv", "--quiet", "--python", "3.12", venv], { stdio: "inherit" });
    else execFileSync(process.env.ZYCODE_PYTHON ?? "python3", ["-m", "venv", venv], { stdio: "inherit" });
  }
  if (useUv) execFileSync("uv", ["pip", "install", "--quiet", "--python", python, ...assets.pythonPackages], { stdio: "inherit" });
  else execFileSync(python, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", ...assets.pythonPackages], { stdio: "inherit" });
  const json = execFileSync(python, [path.join(ROOT, "scripts", "python_licenses.py")], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  return JSON.parse(json);
}

/**
 * The npm packages that esbuild bundled, from the input paths of its metafile. The last `node_modules/`
 * in a path names the package: `node_modules/a/node_modules/b/x.js` is in `b`, not in `a`. There is one
 * entry for each folder, so two versions of one name both show, and each one has its own license text.
 */
export function bundledPackages(inputs) {
  const found = new Map();
  for (const input of inputs) {
    const match = /^(.*(?:^|\/)node_modules\/((?:@[^/]+\/)?[^/]+))\//.exec(input);
    if (match) found.set(match[1], match[2]);
  }
  return [...found]
    .map(([dir, name]) => ({ name, dir }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.dir.localeCompare(b.dir));
}

function npmPackages() {
  const inputs = [];
  for (const name of ["extension", "webview"]) {
    const metaFile = path.join(CACHE_DIR, `meta-${name}.json`);
    if (!existsSync(metaFile)) throw new Error(`${metaFile} is missing: run \`node esbuild.mjs --production --metafile\` first`);
    inputs.push(...Object.keys(readJson(metaFile).inputs));
  }
  const out = [];
  for (const { name, dir: relative } of bundledPackages(inputs)) {
    const dir = path.join(ROOT, relative);
    const manifest = readJson(path.join(dir, "package.json"));
    const files = readdirSync(dir)
      .filter((f) => /^(licen[cs]e|copying|notice)/i.test(f))
      .map((f) => ({ name: f, text: readFileSync(path.join(dir, f), "utf8") }));
    const license = typeof manifest.license === "string" ? manifest.license : (manifest.license?.type ?? "UNKNOWN");
    out.push({ name, version: manifest.version, license, home: manifest.homepage ?? "", files });
  }
  return out;
}

function section(entry) {
  const lines = [`${entry.name} ${entry.version}`, `License: ${entry.license}`];
  if (entry.home) lines.push(entry.home);
  if (entry.files.length === 0) {
    lines.push(
      /^pi-/.test(entry.name)
        ? "(Part of zycode. The license is the MIT license of this extension: see LICENSE.)"
        : "(The package has no license file. See the project page.)",
    );
  }
  for (const file of entry.files) lines.push("", `--- ${file.name} ---`, file.text.trimEnd());
  return lines.join("\n");
}

export function buildNotices() {
  const assets = loadAssets();
  const python = pythonDistributions(assets);
  const npm = npmPackages();
  const unknown = [...python, ...npm].filter((d) => d.license === "UNKNOWN" && !REVIEWED_LICENSES[d.name.toLowerCase()]);
  if (unknown.length > 0) {
    throw new Error(`license is UNKNOWN for: ${unknown.map((d) => d.name).join(", ")}. Review each one and add it to REVIEWED_LICENSES in scripts/notices.mjs.`);
  }
  const rule = "=".repeat(78);
  const parts = [
    "THIRD-PARTY NOTICES for the zycode VS Code extension",
    "",
    "Unofficial; not affiliated with pi or Microsoft. The extension is MIT licensed (LICENSE).",
    "",
    rule,
    `PART 1. The agent program in bin/ (zycode-agent ${assets.version}, a PyInstaller build)`,
    rule,
    "",
    `The list is made from a fresh install of: ${assets.pythonPackages.join(", ")}.`,
    "The versions of the dependencies can differ from the ones inside the binary, which was built on",
    "another day. The licenses are those of the distributions in that install.",
    "",
    CPYTHON_TEXT,
    "",
    PYINSTALLER_TEXT,
    ...python.flatMap((d) => ["", "-".repeat(78), section(d)]),
    "",
    rule,
    "PART 2. npm packages bundled into dist/extension.js and dist/webview.js",
    rule,
    ...npm.flatMap((d) => ["", "-".repeat(78), section(d)]),
    "",
  ];
  return { text: parts.join("\n"), python: python.length, npm: npm.length };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { text, python, npm } = buildNotices();
  writeFileSync(path.join(ROOT, "THIRD-PARTY-NOTICES"), text);
  console.log(`THIRD-PARTY-NOTICES: ${python} Python distributions, ${npm} npm packages, ${text.length} characters`);
}
