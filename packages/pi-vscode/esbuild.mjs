import { build } from "esbuild";

const production = process.argv.includes("--production");
const common = { bundle: true, sourcemap: !production, minify: production, logLevel: "info" };

await build({
  ...common,
  entryPoints: ["src/extension.ts"],
  outfile: "dist/extension.js",
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode"],
});

await build({
  ...common,
  entryPoints: ["src/webview/main.ts"],
  outfile: "dist/webview.js",
  platform: "browser",
  format: "iife",
  target: "es2022",
});
