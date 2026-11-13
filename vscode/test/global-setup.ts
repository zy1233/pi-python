import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** The webview test runs `dist/webview.js`, so build it first. */
export default function setup(): void {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const result = spawnSync(process.execPath, ["esbuild.mjs"], { cwd: root, encoding: "utf8" });
  if (result.status !== 0) throw new Error(`esbuild failed:\n${result.stdout}\n${result.stderr}`);
}
