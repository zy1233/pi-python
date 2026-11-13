// Makes media/icon.png (256x256) from the logo of the site (web/public/favicon.svg), with headless Chrome.
//   node scripts/make-icon.mjs
// The PNG is committed. Run this only when the logo changes. Set ZYCODE_CHROME to choose the browser.
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { REPO_ROOT, ROOT } from "./lib.mjs";

const svg = readFileSync(path.join(REPO_ROOT, "web", "public", "favicon.svg"), "utf8");
const chrome = [process.env.ZYCODE_CHROME, "google-chrome-stable", "chromium", "chromium-browser"]
  .filter(Boolean)
  .find((candidate) => spawnSync(candidate, ["--version"]).status === 0);
if (!chrome) {
  console.error("No Chrome or Chromium found. Set ZYCODE_CHROME.");
  process.exit(1);
}
const dir = mkdtempSync(path.join(tmpdir(), "zycode-icon-"));
const page = path.join(dir, "icon.html");
writeFileSync(
  page,
  `<!DOCTYPE html><html><body style="margin:0;background:transparent"><div style="width:256px;height:256px">${svg.replace("<svg ", '<svg width="256" height="256" ')}</div></body></html>`,
);
const out = path.join(ROOT, "media", "icon.png");
execFileSync(chrome, [
  "--headless=new",
  "--no-sandbox",
  "--disable-gpu",
  "--disable-dev-shm-usage",
  `--user-data-dir=${path.join(dir, "profile")}`,
  "--default-background-color=00000000",
  "--hide-scrollbars",
  "--window-size=256,256",
  `--screenshot=${out}`,
  `file://${page}`,
], { stdio: "ignore" });
rmSync(dir, { recursive: true, force: true });
console.log(out);
