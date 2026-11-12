// Runs dist/webview.js in headless Chrome with a mock of the VS Code API. Skipped when there is no
// Chrome (set ZYCODE_CHROME to a path to choose one; a wrapper script that adds --remote-debugging-port never exits) or no build (`npm run build` first).
import { spawnSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { buildHarness } from "../scripts/webview-harness.mjs";
import { tempDir } from "./helpers.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const built = existsSync(path.join(root, "dist", "webview.js"));

function findChrome(): string | undefined {
  const candidates = [process.env.ZYCODE_CHROME, "google-chrome-stable", "chromium", "chromium-browser", "google-chrome"].filter(
    (c): c is string => Boolean(c),
  );
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return undefined;
}

const chrome = findChrome();
const dirs: string[] = [];

function run(script: string): Record<string, unknown> {
  const dir = tempDir(dirs, "webview-");
  const file = path.join(dir, "page.html");
  writeFileSync(
    file,
    buildHarness({
      demo: false,
      script: `try { ${script}; document.getElementById("result").textContent = JSON.stringify({ ok: true, ...(window.__result ?? {}) }); } catch (e) { document.getElementById("result").textContent = JSON.stringify({ ok: false, error: String(e && e.stack || e) }); }`,
    }),
  );
  const result = spawnSync(
    chrome!,
    ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", `--user-data-dir=${path.join(dir, "profile")}`, "--allow-file-access-from-files", "--virtual-time-budget=3000", "--dump-dom", `file://${file}`],
    { encoding: "utf8", timeout: 20_000 },
  );
  const match = /<pre id="result">([\s\S]*?)<\/pre>/.exec(result.stdout);
  if (!match) throw new Error(`no result in the page. stderr: ${result.stderr.slice(-500)}`);
  const text = match[1]!.replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  return JSON.parse(text) as Record<string, unknown>;
}

const state = `const state = (o) => host({ type: "state", status: "ready", canRestart: true, ...o });`;

describe.skipIf(!chrome || !built)("webview in headless Chrome", () => {
  it("sends ready first and renders markdown as DOM, never as HTML", () => {
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: "hi <img src=x onerror=alert(1)> <script>window.__pwn=1<\\/script> **b** [ok](https://zycode.dev) [bad](file:///etc/passwd)\\n\\n\\\`\\\`\\\`\\n<b>code</b>\\n\\\`\\\`\\\`" });
      const t = document.querySelector("[data-testid=transcript]");
      window.__result = {
        first: window.__posted[0],
        imgs: t.querySelectorAll("img").length,
        scripts: t.querySelectorAll("script").length,
        pwn: window.__pwn ?? null,
        text: t.textContent,
        links: [...t.querySelectorAll("a")].map((a) => a.dataset.url),
        codeBlock: t.querySelector("pre.code code").textContent,
      };`);
    expect(out.ok).toBe(true);
    expect(out.first).toEqual({ type: "ready" });
    expect(out.imgs).toBe(0);
    expect(out.scripts).toBe(0);
    expect(out.pwn).toBeNull();
    expect(out.text).toContain("<img src=x onerror=alert(1)>");
    expect(out.links).toEqual(["https://zycode.dev"]);
    expect(out.codeBlock).toBe("<b>code</b>");
  });

  it("posts send on Enter, not on Shift+Enter, and only when ready", () => {
    const out = run(`${state}
      const input = document.querySelector("textarea");
      state({ status: "starting" });
      input.value = "x"; input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      state({});
      input.value = "line"; input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }));
      const afterShift = window.__posted.filter((m) => m.type === "send").length;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = { afterShift, sends: window.__posted.filter((m) => m.type === "send"), cleared: input.value };`);
    expect(out).toMatchObject({ ok: true, afterShift: 0, sends: [{ type: "send", text: "line" }], cleared: "" });
  });

  it("answers a permission question with the option id, and shows the outcome", () => {
    const out = run(`${state}
      state({});
      host({ type: "permission", requestId: "p1", kind: "tool", title: "bash", summary: "ls", canDiff: false, options: [{ id: "allow-once", name: "Allow once", kind: "allow_once" }, { id: "reject-once", name: "Reject", kind: "reject_once" }] });
      const buttons = [...document.querySelectorAll(".permission button")];
      buttons[0].click();
      host({ type: "permissionDone", requestId: "p1", outcome: "selected", optionId: "allow-once" });
      window.__result = {
        labels: buttons.map((b) => b.textContent),
        answer: window.__posted.find((m) => m.type === "permissionAnswer"),
        buttonsAfter: document.querySelectorAll(".permission button").length,
        done: document.querySelector(".perm-done").textContent,
      };`);
    expect(out).toMatchObject({
      ok: true,
      labels: ["Allow once", "Reject"],
      answer: { type: "permissionAnswer", requestId: "p1", optionId: "allow-once" },
      buttonsAfter: 0,
      done: "Allow once",
    });
  });

  it("shows the setup button, the error detail, and the mode switch only when the agent has a mode option", () => {
    const out = run(`${state}
      state({ status: "needs-setup", detail: "zycode needs a model and an API key. Run the setup in a terminal." });
      document.querySelector(".banner button").click();
      state({ status: "error", detail: "The agent exited (exit code 3).\\nboom" });
      const errorText = document.querySelector(".banner .detail").textContent;
      state({ mode: { current: "ask", options: [{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }] } });
      const select = document.querySelector("select");
      select.value = "auto"; select.dispatchEvent(new Event("change"));
      state({});
      window.__result = { posted: window.__posted.map((m) => m.type), errorText, selectAfter: document.querySelector("select") !== null, modeText: document.querySelector(".controls .mode").textContent };`);
    expect(out).toMatchObject({ ok: true, errorText: "The agent exited (exit code 3).\nboom", selectAfter: false, modeText: "Mode: set by the agent" });
    expect(out.posted).toEqual(["ready", "runSetup", "setMode"]);
  });

  it("sends openLink for a clicked link and nothing for a plain click", () => {
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: "go to [site](https://example.com/x)" });
      document.querySelector("a[data-url]").click();
      window.__result = { link: window.__posted.find((m) => m.type === "openLink") };`);
    expect(out).toMatchObject({ ok: true, link: { type: "openLink", url: "https://example.com/x" } });
  });

  it("offers Show diff on a permission card and on a finished tool card, and says why there is none", () => {
    const out = run(`${state}
      state({});
      const opts = [{ id: "allow-once", name: "Allow once", kind: "allow_once" }];
      host({ type: "permission", requestId: "p1", kind: "tool", toolCallId: "t1", title: "write a", summary: "a", canDiff: true, options: opts });
      host({ type: "permission", requestId: "p2", kind: "tool", toolCallId: "t2", title: "edit b", summary: "b", canDiff: false, diffNote: "diff not available", options: opts });
      host({ type: "toolCall", toolCall: { id: "t3", title: "write c", kind: "edit", status: "completed", canDiff: true } });
      host({ type: "toolCall", toolCall: { id: "t4", title: "write d", kind: "edit", status: "completed" } });
      const labels = [...document.querySelectorAll("button")].filter((b) => b.textContent === "Show diff");
      labels[0].click(); labels[1].click();
      window.__result = {
        count: labels.length,
        posted: window.__posted.filter((m) => m.type === "showDiff"),
        note: document.querySelectorAll(".perm-note")[0].textContent,
      };`);
    expect(out).toMatchObject({
      ok: true,
      count: 2,
      posted: [
        { type: "showDiff", toolCallId: "t1" },
        { type: "showDiff", toolCallId: "t3" },
      ],
      note: "diff not available",
    });
  });

  it("hides the thinking block when showThinking is false, and shows it again", () => {
    const out = run(`${state}
      state({ showThinking: true });
      host({ type: "agentThought", messageId: "a", text: "hmm" });
      const hidden = () => document.querySelector(".thought").hidden;
      const shown = !hidden();
      state({ showThinking: false });
      const afterOff = hidden();
      state({ showThinking: true });
      window.__result = { shown, afterOff, afterOn: hidden() };`);
    expect(out).toMatchObject({ ok: true, shown: true, afterOff: true, afterOn: false });
  });
});
