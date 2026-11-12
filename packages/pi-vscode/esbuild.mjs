import { mkdirSync, writeFileSync } from "node:fs";

import { build } from "esbuild";

const production = process.argv.includes("--production");
const metafile = process.argv.includes("--metafile");
const common = { bundle: true, sourcemap: !production, minify: production, logLevel: "info", metafile };

function saveMeta(name, result) {
  if (!metafile) return;
  mkdirSync(".vsix-cache", { recursive: true });
  writeFileSync(`.vsix-cache/meta-${name}.json`, JSON.stringify(result.metafile));
}

saveMeta(
  "extension",
  await build({
    ...common,
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    platform: "node",
    format: "cjs",
    target: "node20",
    external: ["vscode"],
  }),
);

saveMeta(
  "webview",
  await build({
    ...common,
    entryPoints: ["src/webview/main.ts"],
    outfile: "dist/webview.js",
    platform: "browser",
    format: "iife",
    target: "es2022",
  }),
);
