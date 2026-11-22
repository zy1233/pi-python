// Runs dist/webview.js in headless Chrome with a mock of the VS Code API. Skipped when there is no
// Chrome (set ZYCODE_CHROME to a path to choose one; a wrapper script that adds --remote-debugging-port never exits) or no build (`npm run build` first).
import { spawnSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { buildHarness } from "../scripts/webview-harness.mjs";
import { tempDir } from "./helpers.js";

// Each test invokes Chrome via spawnSync (~1s); yielding a macrotask between tests lets
// Vitest's worker IPC drain onTaskUpdate acknowledgments instead of starving past 60s.
afterEach(() => new Promise((resolve) => setTimeout(resolve, 0)));

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const built = existsSync(path.join(root, "dist", "webview.js"));

function findChrome(): string | undefined {
  const candidates = [
    process.env.ZYCODE_CHROME,
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ].filter((c): c is string => Boolean(c));
  for (const candidate of candidates) {
    if (path.isAbsolute(candidate) && !existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return undefined;
}

const chrome = findChrome();
const dirs: string[] = [];

function run(script: string, beforeScript = ""): Record<string, unknown> {
  const dir = tempDir(dirs, "webview-");
  const file = path.join(dir, "page.html");
  writeFileSync(
    file,
    buildHarness({
      demo: false,
      beforeScript,
      script: `try { ${script}; document.getElementById("result").textContent = JSON.stringify({ ok: true, ...(window.__result ?? {}) }); } catch (e) { document.getElementById("result").textContent = JSON.stringify({ ok: false, error: String(e && e.stack || e) }); }`,
    }),
  );
  const result = spawnSync(
    chrome!,
    ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", `--user-data-dir=${path.join(dir, "profile")}`, "--allow-file-access-from-files", "--virtual-time-budget=3000", "--dump-dom", pathToFileURL(file).href],
    { encoding: "utf8", timeout: 20_000 },
  );
  const match = /<pre id="result">([\s\S]*?)<\/pre>/.exec(result.stdout);
  if (!match) throw new Error(`no result in the page. stderr: ${result.stderr.slice(-500)}`);
  const text = match[1]!.replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  return JSON.parse(text) as Record<string, unknown>;
}

const state = `const state = (o) => host({ type: "state", status: "ready", canRestart: true, ...o });`;

/** A JavaScript string literal for the page script. `<` is escaped, so that `</script>` cannot end the script early. */
const lit = (value: string) => JSON.stringify(value).replace(/</g, "\\u003c");

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

  it("draws the items that follow a deeply nested answer when the chat reloads", () => {
    const deep = "*a ".repeat(5000) + "a* ".repeat(5000);
    const out = run(`${state}
      state({});
      host({ type: "reset", transcript: [
        { type: "agent", id: "a1", text: ${lit(deep)}, thought: "" },
        { type: "notice", level: "info", text: "NOTICE-AFTER" },
      ] });
      window.__result = { text: document.querySelector("[data-testid=transcript]").textContent };`);
    expect(out.ok).toBe(true);
    expect(out.text).toContain("NOTICE-AFTER");
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

  it("renders headings, emphasis, lists, a quote, a rule and a table as elements, with no raw marks left", () => {
    const source = [
      "## Title",
      "**bold** *em* ~~del~~ `code`",
      "- a\n  - b",
      "3. x\n4. y",
      "> quote",
      "---",
      "| h1 | h2 | h3 |\n|:--|:-:|--:|\n| c1 | c2 | c3 |",
    ].join("\n\n");
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit(source)} });
      const body = document.querySelector(".msg.agent .body");
      const q = (s) => body.querySelector(s);
      window.__result = {
        h2: q("h2")?.textContent,
        strong: q("strong")?.textContent, em: q("em")?.textContent, del: q("del")?.textContent, code: q("p code")?.textContent,
        nested: body.querySelectorAll("ul ul li").length,
        olStart: q("ol")?.start, olItems: body.querySelectorAll("ol li").length,
        tightItem: q("ul > li").firstChild.nodeType,
        quote: q("blockquote")?.textContent,
        hr: body.querySelectorAll("hr").length,
        th: [...body.querySelectorAll("table th")].map((c) => c.textContent),
        td: [...body.querySelectorAll("table td")].map((c) => c.textContent),
        align: [...body.querySelectorAll("table th, table td")].map((c) => c.className),
        wrapped: q(".table-wrap > table") !== null,
        marks: /[#*|~>]/.test(body.textContent),
      };`);
    expect(out).toMatchObject({
      ok: true,
      h2: "Title",
      strong: "bold",
      em: "em",
      del: "del",
      code: "code",
      nested: 1,
      olStart: 3,
      olItems: 2,
      tightItem: 3, // a text node: a tight item has no <p>
      quote: "quote",
      hr: 1,
      th: ["h1", "h2", "h3"],
      td: ["c1", "c2", "c3"],
      align: ["align-left", "align-center", "align-right", "align-left", "align-center", "align-right"],
      wrapped: true,
      marks: false,
    });
  });

  it("keeps hostile Markdown from becoming elements: HTML, an image, a javascript: link, a table cell", () => {
    const source = [
      "<img src=x onerror=window.__pwn=1> <iframe src=//example.com></iframe>",
      "<script>window.__pwn=2</script>",
      "![pic](https://example.com/a.png) [bad](javascript:window.__pwn=3)",
      "| a |\n|---|\n| <b>x</b><script>window.__pwn=4</script> |",
    ].join("\n\n");
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit(source)} });
      const body = document.querySelector(".msg.agent .body");
      window.__result = {
        imgs: document.querySelectorAll("img").length,
        frames: document.querySelectorAll("iframe").length,
        scripts: body.querySelectorAll("script").length,
        bold: body.querySelectorAll("b").length,
        pwn: window.__pwn ?? null,
        links: [...body.querySelectorAll("a")].map((a) => ({ url: a.dataset.url, text: a.textContent, href: a.getAttribute("href") })),
        text: body.textContent,
      };`);
    expect(out).toMatchObject({ ok: true, imgs: 0, frames: 0, scripts: 0, bold: 0, pwn: null });
    // The image is a link with its description. It loads nothing. The javascript: link is not a link.
    expect(out.links).toEqual([{ url: "https://example.com/a.png", text: "pic", href: "#" }]);
    expect(out.text).toContain("<iframe src=//example.com></iframe>");
    expect(out.text).toContain("[bad](javascript:window.__pwn=3)");
    expect(out.text).toContain("<b>x</b><script>window.__pwn=4</script>");
  });

  it("shows an address that the host would refuse as text, with the address, and not as a link", () => {
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit("open [the file](vscode://file/etc/hosts) now, or [src/a.ts](src/a.ts)")} });
      const body = document.querySelector(".msg.agent .body");
      window.__result = { links: body.querySelectorAll("a").length, text: body.textContent };`);
    expect(out).toMatchObject({
      ok: true,
      links: 0,
      // The text of a link that is the same as its address is shown once.
      text: "open the file (vscode://file/etc/hosts) now, or src/a.ts",
    });
  });

  it("draws the first piece of a streamed answer at once, joins the next pieces into one later draw, and draws the last one", () => {
    const out = run(`${state}
      state({});
      const text = () => document.querySelector(".msg.agent .body").textContent;
      const bold = () => document.querySelectorAll(".msg.agent .body strong").length;
      host({ type: "agentText", messageId: "a", text: "**one" });
      const first = { text: text(), bold: bold() };
      host({ type: "agentText", messageId: "a", text: "** two" });
      host({ type: "agentText", messageId: "a", text: " three" });
      const during = { text: text(), bold: bold() };
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({ ok: true, first, during, after: { text: text(), bold: bold() } });
      }, 500);`);
    expect(out).toMatchObject({
      ok: true,
      first: { text: "**one", bold: 0 }, // drawn at once; the bold is not closed yet
      during: { text: "**one", bold: 0 }, // the next two pieces wait
      after: { text: "one two three", bold: 1 }, // one draw with both pieces
    });
  });

  it("does not draw an old answer after the chat was reset, and draws the new one at once", () => {
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "old", text: "first" });
      host({ type: "agentText", messageId: "old", text: " second" });
      const oldBody = document.querySelector(".msg.agent .body");
      host({ type: "reset", transcript: [{ type: "agent", id: "new", text: "fresh", thought: "" }] });
      const now = [...document.querySelectorAll(".msg.agent .body")].map((b) => b.textContent);
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({ ok: true, now, oldBody: oldBody.textContent });
      }, 500);`);
    // The planned draw of the old answer was cancelled: its (removed) element never gets " second".
    expect(out).toMatchObject({ ok: true, now: ["fresh"], oldBody: "first" });
  });

  it("does not draw the text again when only the thinking grows", () => {
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: "answer" });
      const body = document.querySelector(".msg.agent .body");
      const node = body.firstChild;
      host({ type: "agentThought", messageId: "a", text: "hm" });
      host({ type: "agentThought", messageId: "a", text: "mm" });
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({ ok: true, same: body.firstChild === node, thought: document.querySelector(".thought-body").textContent });
      }, 500);`);
    expect(out).toMatchObject({ ok: true, same: true, thought: "hmmm" });
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

describe.skipIf(!chrome || !built)("webview: modes, first run and notices", () => {
  const modes = `[{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }, { id: "always-approve", name: "Always approve" }]`;

  it("offers Always approve as the third option and sends it; the question is asked by the extension host", () => {
    const out = run(`${state}
      state({ mode: { current: "ask", options: ${modes} } });
      const select = document.querySelector("select");
      const options = [...select.options].map((o) => o.value);
      select.value = "always-approve"; select.dispatchEvent(new Event("change"));
      window.__result = { options, posted: window.__posted.filter((m) => m.type === "setMode") };`);
    expect(out).toMatchObject({ ok: true, options: ["ask", "auto", "always-approve"], posted: [{ type: "setMode", value: "always-approve" }] });
  });

  const levels = `[{ id: "xhigh", name: "xhigh", description: "Extended reasoning" }, { id: "high", name: "high", description: "Heavy reasoning" }, { id: "low", name: "low", description: "Faster, lighter reasoning" }, { id: "off", name: "off", description: "No reasoning" }]`;

  it("shows the thinking level, sends a change, and keeps the chosen level visible", () => {
    const out = run(`${state}
      state({ mode: { current: "ask", options: ${modes} }, thinking: { current: "low", levels: ${levels} } });
      const select = document.querySelector(".thinking select");
      const before = { options: [...select.options].map((o) => o.value), value: select.value, disabled: select.disabled };
      select.value = "high"; select.dispatchEvent(new Event("change"));
      state({ mode: { current: "ask", options: ${modes} }, thinking: { current: "high", levels: ${levels} } });
      window.__result = { before, after: document.querySelector(".thinking select").value, posted: window.__posted.filter((m) => m.type === "setThinking") };`);
    expect(out).toMatchObject({
      ok: true,
      before: { options: ["xhigh", "high", "low", "off"], value: "low", disabled: false },
      after: "high",
      posted: [{ type: "setThinking", value: "high" }],
    });
  });

  it("shows max when the host lists it, and sends it", () => {
    const withMax = `[{ id: "max", name: "max", description: "Maximum reasoning" }, ${levels.slice(1)}`;
    const out = run(`${state}
      state({ mode: { current: "ask", options: ${modes} }, thinking: { current: "low", levels: ${withMax} } });
      const select = document.querySelector(".thinking select");
      const options = [...select.options].map((o) => o.value);
      select.value = "max"; select.dispatchEvent(new Event("change"));
      window.__result = { options, posted: window.__posted.filter((m) => m.type === "setThinking") };`);
    expect(out).toMatchObject({ ok: true, options: ["max", "xhigh", "high", "low", "off"], posted: [{ type: "setThinking", value: "max" }] });
  });

  it("disables the picker and gives the reason for a model that does not think", () => {
    const reason = "agent.toml sets reasoning = false for the model.";
    const out = run(`${state}
      state({ thinking: { current: "off", levels: ${levels}, disabled: ${JSON.stringify(reason)} } });
      const select = document.querySelector(".thinking select");
      window.__result = { disabled: select.disabled, title: select.title, label: document.querySelector(".thinking").title };`);
    expect(out).toMatchObject({ ok: true, disabled: true, title: reason, label: reason });
  });

  it("shows no thinking picker while the agent is not up, and an unknown level when the file cannot be read", () => {
    const out = run(`${state}
      state({ status: "starting" });
      const none = document.querySelector(".thinking") === null;
      state({ thinking: { levels: ${levels}, disabled: "agent.toml does not exist yet." } });
      const select = document.querySelector(".thinking select");
      window.__result = { none, value: select.value, text: select.selectedOptions[0].textContent, disabled: select.disabled };`);
    expect(out).toMatchObject({ ok: true, none: true, value: "", text: "unknown", disabled: true });
  });

  it("shows a visible label and a warning style while Always approve is on, and removes them after", () => {
    const out = run(`${state}
      state({ mode: { current: "always-approve", options: ${modes} } });
      const chip = document.querySelector(".warn-chip");
      const on = { chip: chip && chip.textContent, cls: document.getElementById("root").classList.contains("always-approve") };
      state({ mode: { current: "ask", options: ${modes} } });
      window.__result = { on, off: { chip: document.querySelector(".warn-chip") !== null, cls: document.getElementById("root").classList.contains("always-approve") } };`);
    expect(out).toMatchObject({ ok: true, on: { chip: "Always approve: no questions", cls: true }, off: { chip: false, cls: false } });
  });

  it("shows the first-run steps, and the buttons run the setup and open the guide", () => {
    const out = run(`${state}
      state({ status: "needs-setup", setup: { reason: "no-model" }, detail: "zycode has no model yet." });
      const steps = document.querySelectorAll(".banner .steps li").length;
      const labels = [...document.querySelectorAll(".banner button")].map((b) => b.textContent);
      document.querySelectorAll(".banner button")[0].click();
      document.querySelectorAll(".banner button")[1].click();
      window.__result = { steps, labels, posted: window.__posted.map((m) => m.type), text: document.querySelector(".banner").textContent };`);
    expect(out).toMatchObject({ ok: true, steps: 3, labels: ["Run setup", "Open the guide"] });
    expect(out.posted).toEqual(["ready", "runSetup", "openGuide"]);
    expect(String(out.text)).toMatch(/Never type it in this chat/);
  });

  it("renders the buttons of a notice and sends the action", () => {
    const out = run(`${state}
      state({});
      host({ type: "notice", level: "warning", text: "The key was refused.", actions: [{ id: "runSetup", label: "Change the key" }, { id: "openAgentConfig", label: "Open agent.toml" }, { id: "openLog", label: "Show log" }] });
      const buttons = [...document.querySelectorAll(".notice button")];
      buttons.forEach((b) => b.click());
      window.__result = { labels: buttons.map((b) => b.textContent), posted: window.__posted.map((m) => m.type).slice(1) };`);
    expect(out).toMatchObject({ ok: true, labels: ["Change the key", "Open agent.toml", "Show log"], posted: ["runSetup", "openAgentConfig", "openLog"] });
  });

  it("a notice with no action has no button", () => {
    const out = run(`${state}
      state({});
      host({ type: "notice", level: "info", text: "Permission mode: auto." });
      window.__result = { buttons: document.querySelectorAll(".notice button").length };`);
    expect(out).toMatchObject({ ok: true, buttons: 0 });
  });

  describe("status row", () => {
    it("names the folder in the meta row, with the full path as the tooltip, and has no context row when the agent sent no usage", () => {
      const out = run(`${state}
        state({ agent: { version: "0.9.1", source: "bundled" }, model: "deepseek-flash", cwd: ${lit("D:\\work\\pi-python")} });
        const meta = document.querySelector(".meta");
        window.__result = { meta: meta.textContent, tip: meta.title, usage: document.querySelector("[data-testid=usage]") !== null, clock: document.querySelector(".clock") !== null };`);
      expect(out).toMatchObject({ ok: true, meta: "agent 0.9.1 · deepseek-flash · folder pi-python", tip: "D:\\work\\pi-python", usage: false, clock: false });
    });

    it("draws the context row from the numbers of the agent, with the cost", () => {
      const out = run(`${state}
        state({ usage: { used: 54200, size: 128000, cost: { amount: 0.12, currency: "USD" } } });
        const row = document.querySelector("[data-testid=usage]");
        const meter = row.querySelector("meter");
        window.__result = {
          cls: row.className, tip: row.title,
          label: row.querySelector(".usage-label").textContent, text: row.querySelector(".usage-text").textContent, cost: row.querySelector(".usage-cost").textContent,
          meter: { min: meter.min, max: meter.max, value: meter.value, label: meter.getAttribute("aria-label") },
          inHeader: row.parentElement.classList.contains("header"),
        };`);
      expect(out).toMatchObject({
        ok: true,
        cls: "usage ok",
        tip: "54,200 of 128,000 tokens are in the context (42%). The agent counts them.",
        label: "Context",
        text: "42% · 54.2k of 128k tokens",
        cost: "$0.12",
        meter: { min: 0, max: 128000, value: 54200, label: "Context used" },
        inHeader: true,
      });
    });

    it("marks a window that is nearly full, shows only the count when the size is not known, and drops the row when the usage goes", () => {
      const out = run(`${state}
        const levelOf = (usage) => { state({ usage }); return document.querySelector("[data-testid=usage]").className; };
        const ok = levelOf({ used: 79000, size: 100000 });
        const warn = levelOf({ used: 80000, size: 100000 });
        const high = levelOf({ used: 89000, size: 100000 });
        const danger = levelOf({ used: 90000, size: 100000 });
        const over = levelOf({ used: 112500, size: 100000 });
        const plain = levelOf({ used: 54200 });
        const row = document.querySelector("[data-testid=usage]");
        const plainView = { text: row.querySelector(".usage-text").textContent, meter: row.querySelector("meter") !== null, cost: row.querySelector(".usage-cost") !== null };
        state({ usage: { used: 112500, size: 100000 } });
        const overRow = document.querySelector("[data-testid=usage]");
        const overView = { text: overRow.querySelector(".usage-text").textContent, value: overRow.querySelector("meter").value };
        state({});
        window.__result = { levels: [ok, warn, high, danger, over, plain], plainView, overView, gone: document.querySelector("[data-testid=usage]") === null };`);
      expect(out).toMatchObject({
        ok: true,
        levels: ["usage ok", "usage warn", "usage warn", "usage danger", "usage danger", "usage plain"],
        plainView: { text: "54.2k tokens", meter: false, cost: false },
        overView: { text: "113% · 113k of 100k tokens", value: 100000 }, // the bar stops at full
        gone: true,
      });
    });

    it("writes what the agent sends as text, never as HTML", () => {
      const out = run(`${state}
        state({ usage: { used: 1, size: 2, cost: { amount: 1, currency: "<img src=x onerror=alert(1)>" } }, cwd: ${lit("/home/u/<b>x</b>")} });
        window.__result = { imgs: document.querySelectorAll("img").length, bold: document.querySelectorAll(".header b").length, cost: document.querySelector(".usage-cost").textContent };`);
      expect(out).toMatchObject({ ok: true, imgs: 0, bold: 0, cost: "1 <img src=x onerror=alert(1)>" });
    });

    it("counts the running turn with its own clock and leaves the menus alone, then shows how long the turn took", () => {
      const modes = `[{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }]`;
      const out = run(`${state}
        state({ status: "busy", turn: { elapsedMs: 5000 }, mode: { current: "ask", options: ${modes} } });
        const select = document.querySelector(".controls select");
        const first = { text: document.querySelector(".clock").textContent, tip: document.querySelector(".clock").title, status: document.querySelector(".status").textContent };
        setTimeout(() => {
          const later = document.querySelector(".clock").textContent;
          const sameSelect = document.querySelector(".controls select") === select;
          state({ status: "ready", turn: { lastMs: 8432 }, mode: { current: "ask", options: ${modes} } });
          const ended = { text: document.querySelector(".clock").textContent, tip: document.querySelector(".clock").title };
          state({ status: "ready" });
          document.getElementById("result").textContent = JSON.stringify({ ok: true, first, later, sameSelect, ended, none: document.querySelector(".clock") === null });
        }, 2200);`);
      expect(out).toMatchObject({
        ok: true,
        first: { text: "5 s", tip: "Time since you sent the message", status: "Working" },
        later: "7 s", // 5 s from the host and 2 s of the clock of the page
        sameSelect: true, // a tick changes the one element, so a menu that is open stays open
        ended: { text: "last turn 8.4 s", tip: "How long the last turn took" },
        none: true,
      });
    });
  });
});

const mockClipboard = `Object.defineProperty(navigator, "clipboard", { value: { writeText: (t) => { window.__clipboardText = t; return Promise.resolve(); } }, configurable: true });`;

describe.skipIf(!chrome || !built)("webview: code blocks, copy interactions, and IME guard", () => {
  it("renders code block headers, whole-block copy, single-height lines, and preserves code textContent", () => {
    const out = run(`${mockClipboard}
      ${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit("```ts\nconst a = 1;\nconst b = 2;\n```\n\n```\nplain\n```")} });
      const langs = [...document.querySelectorAll(".code-lang")].map((n) => n.dataset.lang);
      const codes = [...document.querySelectorAll("pre.code code")].map((n) => n.textContent);
      const lineRects = [...document.querySelectorAll(".code-block:first-of-type .code-line")].map((n) => n.getBoundingClientRect().height);
      const codeHeight = document.querySelector(".code-block:first-of-type pre.code code").getBoundingClientRect().height;
      const btn = document.querySelector(".code-copy");
      btn.click();
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          langs,
          codes,
          lineRects,
          codeHeight,
          copied: window.__copied,
          clipboardText: window.__clipboardText,
          hasCopiedAttr: btn.hasAttribute("data-copied"),
          btnLabel: btn.dataset.label,
          btnText: btn.textContent,
        });
      }, 50);`);
    expect(out).toMatchObject({
      ok: true,
      langs: ["ts", "code"],
      codes: ["const a = 1;\nconst b = 2;", "plain"],
      copied: "const a = 1;\nconst b = 2;",
      clipboardText: "const a = 1;\nconst b = 2;",
      hasCopiedAttr: true,
      btnLabel: "Copied",
      btnText: "",
    });
    const lineRects = out.lineRects as number[];
    expect(lineRects).toHaveLength(2);
    // Ensure the bare "\n" text node between .code-line items does not add extra vertical height.
    expect(out.codeHeight as number).toBeCloseTo(lineRects[0]! + lineRects[1]!, 0);
  });

  it("copies individual code lines and strips shell prompts in shell blocks", () => {
    const out = run(`${mockClipboard}
      ${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit("```bash\n$ npm install\n\n  $ npm test  \n```")} });
      const lineButtons = [...document.querySelectorAll(".line-copy")];
      lineButtons[0].click();
      const first = window.__copied;
      lineButtons[1].click();
      const second = window.__copied;
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          count: lineButtons.length,
          first,
          second,
          codeText: document.querySelector("pre.code code").textContent,
          secondLabel: lineButtons[1].dataset.label,
        });
      }, 50);`);
    expect(out).toMatchObject({
      ok: true,
      count: 2,
      first: "npm install",
      second: "npm test",
      codeText: "$ npm install\n\n  $ npm test  ",
      secondLabel: "Copied",
    });
  });

  it("copies inline code on click without hijacking inline code inside links", () => {
    const out = run(`${mockClipboard}
      ${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit("run `git status` or see [`docs`](https://zycode.dev/docs)")} });
      const codes = [...document.querySelectorAll("code.inline-code")];
      codes[0].click();
      const afterInline = window.__copied;
      codes[1].click();
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          afterInline,
          afterLink: window.__copied,
          copiedAttr: codes[0].hasAttribute("data-copied"),
          linkCopiedAttr: codes[1].hasAttribute("data-copied"),
          links: window.__posted.filter((m) => m.type === "openLink"),
        });
      }, 50);`);
    expect(out).toMatchObject({
      ok: true,
      afterInline: "git status",
      afterLink: "git status",
      copiedAttr: true,
      linkCopiedAttr: false,
      links: [{ type: "openLink", url: "https://zycode.dev/docs" }],
    });
  });

  it("copies tool call command/title, tool output, permission summary, and whole messages", () => {
    const out = run(`${mockClipboard}
      ${state}
      state({});
      host({ type: "userMessage", id: "u1", text: "check status" });
      host({ type: "agentText", messageId: "a1", text: "Here is the **result**." });
      host({ type: "toolCall", toolCall: { id: "t1", title: "run ls", kind: "execute", status: "completed", rawInput: { command: "ls -la" }, terminal: { output: ${lit("a.txt\n")}, exitCode: 0 } } });
      host({ type: "permission", requestId: "p1", kind: "tool", toolCallId: "t2", title: "bash", summary: "rm -rf /tmp/x", canDiff: false, options: [{ id: "allow-once", name: "Allow once", kind: "allow_once" }] });
      const permButtons = document.querySelectorAll(".permission button").length;
      document.querySelector(".msg.user .msg-copy").click();
      const userCopied = window.__copied;
      document.querySelector(".msg.agent .msg-copy").click();
      const agentCopied = window.__copied;
      document.querySelector(".tool-copy").click();
      const toolCopied = window.__copied;
      document.querySelector(".output-copy").click();
      const outputCopied = window.__copied;
      document.querySelector(".perm-summary").click();
      const permCopied = window.__copied;
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          permButtons,
          userCopied,
          agentCopied,
          toolCopied,
          outputCopied,
          permCopied,
          permLabel: document.querySelector(".perm-summary").dataset.label,
          toolStatus: document.querySelector(".tool").dataset.status,
        });
      }, 50);`);
    expect(out).toMatchObject({
      ok: true,
      permButtons: 1,
      userCopied: "check status",
      agentCopied: "Here is the **result**.",
      toolCopied: "ls -la",
      outputCopied: "a.txt\n",
      permCopied: "rm -rf /tmp/x",
      permLabel: "Copied",
      toolStatus: "completed",
    });
  });

  it("reports failure state cleanly when clipboard access is denied", () => {
    const out = run(`${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit("```sh\necho hi\n```")} });
      const btn = document.querySelector(".code-copy");
      btn.click();
      setTimeout(() => {
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          hasFailed: btn.hasAttribute("data-failed"),
          label: btn.dataset.label,
        });
      }, 50);`);
    expect(out).toMatchObject({ ok: true, hasFailed: true, label: "Copy failed" });
  });

  it("blocks Enter submit during IME composition including Safari keyCode 229 fallback", () => {
    const out = run(`${state}
      state({});
      const input = document.querySelector("textarea");
      input.value = "你好";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }));
      const afterComposing = window.__posted.filter((m) => m.type === "send").length;
      const ev229 = new KeyboardEvent("keydown", { key: "Enter", bubbles: true });
      Object.defineProperty(ev229, "keyCode", { get: () => 229 });
      input.dispatchEvent(ev229);
      const after229 = window.__posted.filter((m) => m.type === "send").length;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = { afterComposing, after229, sends: window.__posted.filter((m) => m.type === "send") };`);
    expect(out).toMatchObject({
      ok: true,
      afterComposing: 0,
      after229: 0,
      sends: [{ type: "send", text: "你好" }],
    });
  });
});

describe.skipIf(!chrome || !built)("webview: slash commands and @ mentions", () => {
  const richState = `${state}
    const modes = [{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }];
    const models = [{ id: "gpt-4o", name: "GPT-4o" }, { id: "deepseek-chat", name: "DeepSeek Chat" }];
    const commands = [
      { name: "goal", description: "Start goal-driven planning", inputHint: "<desc>" },
      { name: "auto-log", description: "Show Auto decisions", inputHint: "<args>" },
    ];
    state({ mode: { current: "ask", options: modes }, models: { current: "gpt-4o", options: models }, commands });`;

  it("opens slash popover, navigates with arrows/ARIA, inserts agent commands, and dismisses with Escape", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      input.value = "/";
      input.setSelectionRange(1, 1);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const openState = {
        hidden: menu.hidden,
        controls: input.getAttribute("aria-controls"),
        listbox: document.getElementById(input.getAttribute("aria-controls") ?? "")?.getAttribute("role"),
        role: input.getAttribute("role"),
        activeDesc0: input.getAttribute("aria-activedescendant"),
        options: [...menu.querySelectorAll("[role='option']")].map((el) => el.querySelector(".popover-title").textContent),
      };
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      const afterDown = {
        activeDesc: input.getAttribute("aria-activedescendant"),
        selected: [...menu.querySelectorAll("[role='option']")].map((el) => el.getAttribute("aria-selected")),
      };
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const afterUp = input.getAttribute("aria-activedescendant");
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterAccept = {
        value: input.value,
        menuHidden: menu.hidden,
        sends: window.__posted.filter((m) => m.type === "send").length,
      };
      input.value = "/auto";
      input.setSelectionRange(5, 5);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const openBeforeEsc = !menu.hidden;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      window.__result = {
        openState,
        afterDown,
        afterUp,
        afterAccept,
        openBeforeEsc,
        hiddenAfterEsc: menu.hidden,
        activeDescAfterEsc: input.getAttribute("aria-activedescendant"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      openState: { hidden: false, controls: "composer-listbox", listbox: "listbox", role: null, activeDesc0: "composer-opt-0" },
      afterDown: { activeDesc: "composer-opt-1" },
      afterUp: "composer-opt-0",
      afterAccept: { value: "/goal ", menuHidden: true, sends: 0 },
      openBeforeEsc: true,
      hiddenAfterEsc: true,
      activeDescAfterEsc: null,
    });
    const opts = (out.openState as { options: string[] }).options;
    expect(opts.slice(0, 3)).toEqual(["/goal", "/auto-log", "/new"]);
    const selected = (out.afterDown as { selected: string[] }).selected;
    expect(selected[0]).toBe("false");
    expect(selected[1]).toBe("true");
  });

  it("supports Stage 2 /mode completion and immediate /new execution", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      input.value = "/mode";
      input.setSelectionRange(5, 5);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const stage2Value = input.value;
      const stage2Options = [...menu.querySelectorAll("[role='option'] .popover-title")].map((n) => n.textContent);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterMode = { value: input.value, hidden: menu.hidden };

      input.value = "/new";
      input.setSelectionRange(4, 4);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = {
        stage2Value,
        stage2Options,
        afterMode,
        afterNew: { value: input.value, hidden: menu.hidden },
        posted: window.__posted.slice(1),
      };`);
    expect(out).toMatchObject({
      ok: true,
      stage2Value: "/mode ",
      stage2Options: ["Ask", "Auto"],
      afterMode: { value: "", hidden: true },
      afterNew: { value: "", hidden: true },
      posted: [
        { type: "setMode", value: "auto" },
        { type: "newChat" },
      ],
    });
  });

  it("queries @ mentions, highlights matches, drills folders with Tab, manages chips, and submits with mentions", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const chips = document.querySelector(".composer-chips");
      const lastQuery = () => [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);

      input.value = "check @app";
      input.setSelectionRange(10, 10);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const q1 = lastQuery();
      host({
        type: "mentionResults",
        requestId: q1.requestId,
        query: q1.query,
        items: [
          { kind: "file", path: "src/app.ts", label: "app.ts", detail: "src", labelMatches: [0, 1, 2] },
          { kind: "folder", path: "src/core/", label: "src/core/", detail: "Folder", badge: "folder" },
        ],
      });
      const marks = [...menu.querySelectorAll("mark.match")].map((m) => m.textContent);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterSelectFile = {
        value: input.value,
        chipsHidden: chips.hidden,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
      };

      // Drill folder with Tab: replaces token without trailing space and without adding a chip.
      input.value += "@core";
      input.setSelectionRange(input.value.length, input.value.length);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const q2 = lastQuery();
      host({
        type: "mentionResults",
        requestId: q2.requestId,
        query: q2.query,
        items: [
          { kind: "folder", path: "src/core/", label: "src/core/", detail: "Folder", badge: "folder" },
        ],
      });
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
      const afterDrill = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
        drillQuery: lastQuery().query,
      };
      host({
        type: "mentionResults",
        requestId: lastQuery().requestId,
        query: "src/core/",
        items: [
          { kind: "file", path: "src/core/chat.ts", label: "chat.ts", detail: "src/core" },
        ],
      });
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const twoChips = [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent);

      // Remove first chip and verify both chip and inline @src/app.ts token are removed.
      chips.querySelector(".chip-remove").click();
      const afterRemove = {
        value: input.value,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
      };

      // Submit with remaining chip.
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = {
        marks,
        afterSelectFile,
        afterDrill,
        twoChips,
        afterRemove,
        afterSubmit: { value: input.value, chipsHidden: chips.hidden },
        sends: window.__posted.filter((m) => m.type === "send"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      marks: ["app"],
      afterSelectFile: { value: "check @src/app.ts ", chipsHidden: false, chipLabels: ["app.ts"] },
      afterDrill: { value: "check @src/app.ts @src/core/", chipCount: 1, drillQuery: "src/core/" },
      twoChips: ["app.ts", "chat.ts"],
      afterRemove: { value: "check @src/core/chat.ts ", chipLabels: ["chat.ts"] },
      afterSubmit: { value: "", chipsHidden: true },
      sends: [
        {
          type: "send",
          text: "check @src/core/chat.ts ",
          mentions: [{ kind: "file", path: "src/core/chat.ts" }],
        },
      ],
    });
  });

  it("opens popovers from @ and / quick-trigger buttons and respects IME guard while open", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const mentionBtn = document.querySelector("[aria-label='Mention file, folder, or selection']");
      const slashBtn = document.querySelector("[aria-label='Slash commands']");

      slashBtn.click();
      const slashOpen = { value: input.value, hidden: menu.hidden, head: menu.querySelector(".popover-head").textContent };

      // Guard Enter while IME is composing or keyCode is 229 when popover is open.
      const beforeImeLen = window.__posted.length;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }));
      const ev229 = new KeyboardEvent("keydown", { key: "Enter", bubbles: true });
      Object.defineProperty(ev229, "keyCode", { get: () => 229 });
      input.dispatchEvent(ev229);
      const afterIme = { value: input.value, hidden: menu.hidden, postedSame: window.__posted.length === beforeImeLen };

      input.value = "explain";
      input.setSelectionRange(7, 7);
      mentionBtn.click();
      const mentionOpen = {
        value: input.value,
        hidden: menu.hidden,
        head: menu.querySelector(".popover-head").textContent,
        query: [...window.__posted].filter((m) => m.type === "queryMentions").at(-1)?.query,
      };
      window.__result = { slashOpen, afterIme, mentionOpen };`);
    expect(out).toMatchObject({
      ok: true,
      slashOpen: { value: "/", hidden: false, head: "Commands" },
      afterIme: { value: "/", hidden: false, postedSame: true },
      mentionOpen: { value: "explain @", hidden: false, head: "References · files, folders, selection", query: "" },
    });
  });

  it("renders hostile command descriptions and file paths as text, never as HTML", () => {
    const out = run(`${state}
      state({
        commands: [{ name: "evil", description: ${lit("<img src=x onerror=window.__pwn=1><script>window.__pwn=2</script>")} }],
      });
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      input.value = "/evil";
      input.setSelectionRange(5, 5);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const cmdImgs = menu.querySelectorAll("img").length;
      const cmdScripts = menu.querySelectorAll("script").length;
      const cmdDesc = menu.querySelector(".popover-detail").textContent;

      input.value = "@xss";
      input.setSelectionRange(4, 4);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const req = [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      host({
        type: "mentionResults",
        requestId: req.requestId,
        query: req.query,
        items: [{
          kind: "file",
          path: ${lit("<b>xss</b>/<img src=x onerror=window.__pwn=3>.ts")},
          label: ${lit("<img src=x onerror=window.__pwn=3>.ts")},
          detail: ${lit("<b>xss</b>")},
        }],
      });
      window.__result = {
        cmdImgs,
        cmdScripts,
        cmdDesc,
        mentionImgs: menu.querySelectorAll("img").length,
        mentionScripts: menu.querySelectorAll("script").length,
        mentionBold: menu.querySelectorAll("b").length,
        pwn: window.__pwn ?? null,
        mentionText: menu.textContent,
      };`);
    expect(out).toMatchObject({
      ok: true,
      cmdImgs: 0,
      cmdScripts: 0,
      cmdDesc: "<img src=x onerror=window.__pwn=1><script>window.__pwn=2</script>",
      mentionImgs: 0,
      mentionScripts: 0,
      mentionBold: 0,
      pwn: null,
    });
    expect(String(out.mentionText)).toContain("<img src=x onerror=window.__pwn=3>.ts");
  });

  it("keeps trailing Chinese text when picking mid-sentence @ mentions, preserves draft on client notice or / button, blocks stale Enter while loading, and keeps /mode title readable at 320px", () => {
    const out = run(`
      document.body.style.width = "320px";
      document.getElementById("root").style.width = "320px";
      ${richState}
      state({
        mode: {
          current: "ask",
          options: [
            { id: "ask", name: "Ask" },
            { id: "auto", name: "Auto" },
            { id: "always-approve", name: "Always approve" },
          ],
        },
        commands,
      });
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const chips = document.querySelector(".composer-chips");
      const slashBtn = document.querySelector("[aria-label='Slash commands']");

      // B1: mid-sentence @ mention in Chinese preserves trailing text.
      input.value = "解释@ma然后改一下这个文件";
      input.setSelectionRange(5, 5);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const qMa = [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      host({
        type: "mentionResults",
        requestId: qMa.requestId,
        query: qMa.query,
        items: [{ kind: "file", path: "src/main.ts", label: "main.ts", detail: "src" }],
      });
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterMidSentence = input.value;
      chips.querySelector(".chip-remove").click();

      // NB #1: no-arg client command with trailing words keeps draft in composer.
      input.value = "/config does not load, please fix";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterConfigNotice = {
        value: input.value,
        notice: [...document.querySelectorAll(".notice")].at(-1)?.textContent,
        sends: window.__posted.filter((m) => m.type === "send").length,
      };

      // NB #2: stale @ results cannot be accepted on Enter while a newer query is loading.
      input.value = "@";
      input.setSelectionRange(1, 1);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const qFirst = [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      host({
        type: "mentionResults",
        requestId: qFirst.requestId,
        query: "",
        items: [{ kind: "file", path: "a/first.txt", label: "first.txt", detail: "a" }],
      });
      input.value = "@zzz";
      input.setSelectionRange(4, 4);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterStaleEnter = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
        sends: window.__posted.filter((m) => m.type === "send").length,
      };

      // NB #3: / button opens command list without mutating non-slash draft; picking inserts prefix.
      input.value = "explain this";
      input.setSelectionRange(12, 12);
      slashBtn.click();
      const afterSlashBtn = { hidden: menu.hidden, value: input.value };
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterEscSend = {
        value: input.value,
        sentText: window.__posted.filter((m) => m.type === "send").at(-1)?.text,
      };
      input.value = "explain this";
      input.setSelectionRange(12, 12);
      slashBtn.click();
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterPickGoal = { value: input.value, hidden: menu.hidden };

      // NB #11: /mode title does not truncate next to "ask | auto | always-approve" at 320px.
      input.value = "/mo";
      input.setSelectionRange(3, 3);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const modeTitle = [...menu.querySelectorAll(".popover-title")].find((el) => el.textContent === "/mode");
      const modeNotClipped = modeTitle ? modeTitle.scrollWidth <= modeTitle.clientWidth + 1 : false;

      window.__result = {
        afterMidSentence,
        afterConfigNotice,
        afterStaleEnter,
        afterSlashBtn,
        afterEscSend,
        afterPickGoal,
        modeNotClipped,
      };`);
      expect(out).toMatchObject({
      ok: true,
      afterMidSentence: "解释@src/main.ts 然后改一下这个文件",
      afterConfigNotice: {
        value: "/config does not load, please fix",
        notice: "/config takes no arguments.",
        sends: 0,
      },
      afterStaleEnter: {
        value: "@zzz",
        chipCount: 0,
        sends: 0,
      },
      afterSlashBtn: { hidden: false, value: "explain this" },
      afterEscSend: { value: "", sentText: "explain this" },
      afterPickGoal: { value: "/goal explain this", hidden: true },
      modeNotClipped: true,
    });
  });

  it("handles / button no-arg client commands on a draft, client commands with @ chips, Shift+Tab, secret badge, and mention loading timeout", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const chips = document.querySelector(".composer-chips");
      const slashBtn = document.querySelector("[aria-label='Slash commands']");
      const lastQuery = () => [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);

      // 1. Attach an @ chip with a secret badge, verify "may hold secrets" badge and Shift+Tab does not accept.
      input.value = "explain @env";
      input.setSelectionRange(12, 12);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const qEnv = lastQuery();
      host({
        type: "mentionResults",
        requestId: qEnv.requestId,
        query: qEnv.query,
        items: [{ kind: "file", path: ".env", label: ".env", detail: "", badge: "secret" }],
      });
      const secretBadgeText = menu.querySelector(".popover-badge.secret")?.textContent;
      const shiftTabEv = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, cancelable: true, bubbles: true });
      input.dispatchEvent(shiftTabEv);
      const afterShiftTab = {
        defaultPrevented: shiftTabEv.defaultPrevented,
        value: input.value,
        menuHidden: menu.hidden,
      };
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterAttachSecret = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
      };

      // 2. Click / button on non-empty draft ("explain @.env ") and pick /log (no-arg client command):
      // executes openLog immediately while keeping the user's draft and attached chip intact.
      slashBtn.click();
      const logIdx = [...menu.querySelectorAll("[role='option'] .popover-title")].findIndex((n) => n.textContent === "/log");
      menu.querySelectorAll("[role='option']")[logIdx].click();
      const afterSlashBtnLog = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
        openLogCount: window.__posted.filter((m) => m.type === "openLog").length,
      };

      // 3. Click / button on non-empty draft with attached chip and pick /new:
      // executes newChat while preserving both the draft and the attached chip.
      slashBtn.click();
      const newIdx = [...menu.querySelectorAll("[role='option'] .popover-title")].findIndex((n) => n.textContent === "/new");
      menu.querySelectorAll("[role='option']")[newIdx].click();
      const afterSlashBtnNew = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
        chipsHidden: chips.hidden,
        newChatCount: window.__posted.filter((m) => m.type === "newChat").length,
      };

      // 4. mentionLoading times out after 1000ms if host never answers queryMentions, unblocking Enter to submit.
      input.value = "hello @unanswered";
      input.setSelectionRange(input.value.length, input.value.length);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const statusBeforeTimeout = menu.querySelector(".popover-empty")?.textContent;
      setTimeout(() => {
        const statusAfterTimeout = menu.querySelector(".popover-empty")?.textContent;
        input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          secretBadgeText,
          afterShiftTab,
          afterAttachSecret,
          afterSlashBtnLog,
          afterSlashBtnNew,
          statusBeforeTimeout,
          statusAfterTimeout,
          sends: window.__posted.filter((m) => m.type === "send"),
        });
      }, 1100);`);
    expect(out).toMatchObject({
      ok: true,
      secretBadgeText: "may hold secrets",
      afterShiftTab: {
        defaultPrevented: false,
        value: "explain @env",
        menuHidden: false,
      },
      afterAttachSecret: {
        value: "explain @.env ",
        chipCount: 1,
      },
      afterSlashBtnLog: {
        value: "explain @.env ",
        chipCount: 1,
        openLogCount: 1,
      },
      afterSlashBtnNew: {
        value: "explain @.env ",
        chipCount: 1,
        chipsHidden: false,
        newChatCount: 1,
      },
      statusBeforeTimeout: "Searching workspace…",
      statusAfterTimeout: "No matching files or folders",
      sends: [{ type: "send", text: "hello @unanswered" }],
    });
  });

  it("replaces the whole CJK file name when the cursor is inside it, and still keeps the text of a sentence", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");
      const lastQuery = () => [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      const pick = (text, cursor, path) => {
        input.value = text;
        input.setSelectionRange(cursor, cursor);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        const q = lastQuery();
        host({ type: "mentionResults", requestId: q.requestId, query: q.query, items: [{ kind: "file", path, label: path, detail: "" }] });
        input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        const value = input.value;
        chips.querySelector(".chip-remove")?.click();
        return value;
      };
      window.__result = {
        cjkName: pick("看@文件.md然后", 3, "文件.md"),
        sentence: pick("解释@ma然后改一下这个文件", 5, "src/main.ts"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      cjkName: "看@文件.md 然后",
      sentence: "解释@src/main.ts 然后改一下这个文件",
    });
  });

  it("keeps the textarea a textbox and the listbox free of non-option children, and announces the number of results", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const live = () => [...document.querySelectorAll(".composer [role='status']")].map((n) => n.textContent).filter(Boolean);
      const shape = () => {
        const list = document.getElementById("composer-listbox");
        return {
          listChildRoles: list ? [...list.children].map((c) => c.getAttribute("role")) : null,
          headHidden: menu.querySelector(".popover-head")?.getAttribute("aria-hidden"),
          listLabel: list?.getAttribute("aria-label") ?? null,
          emptyInsideList: Boolean(list?.querySelector(".popover-empty")),
          live: live(),
          controls: input.getAttribute("aria-controls"),
          controlsResolves: input.hasAttribute("aria-controls") ? Boolean(document.getElementById(input.getAttribute("aria-controls"))) : null,
        };
      };
      const closed = shape();
      const typeText = (text) => {
        input.value = text;
        input.setSelectionRange(text.length, text.length);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      };
      typeText("/mode");
      const slash = shape();
      typeText("/zzzzzz");
      const empty = shape();
      typeText("@a");
      const q = [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      host({ type: "mentionResults", requestId: q.requestId, query: q.query, items: [{ kind: "file", path: "a.ts", label: "a.ts", detail: "" }] });
      const mention = shape();
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const afterEsc = shape();
      window.__result = { role: input.getAttribute("role"), closed, slash, empty, mention, afterEsc, afterClose: live() };`);
    expect(out).toMatchObject({
      ok: true,
      role: null,
      closed: { controls: null, controlsResolves: null },
      slash: { listChildRoles: ["option", "option", "option"], headHidden: "true", listLabel: "Commands", emptyInsideList: false, live: ["3 suggestions"], controls: "composer-listbox", controlsResolves: true },
      empty: { listChildRoles: null, emptyInsideList: false, live: ["No matching commands"], controls: null, controlsResolves: null },
      mention: { listChildRoles: ["option"], listLabel: "References · files, folders, selection", live: ["1 suggestion"], controls: "composer-listbox", controlsResolves: true },
      afterEsc: { controls: null, controlsResolves: null },
      afterClose: [],
    });
  });

  it("opens the menus for a full-width ＠ and ／, and the placeholder names the buttons for an input method", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const type = (text) => {
        input.value = text;
        input.setSelectionRange(text.length, text.length);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      };
      type("看＠");
      const at = { hidden: menu.hidden, head: menu.querySelector(".popover-head")?.textContent };
      type("");
      type("／");
      const slash = { hidden: menu.hidden, head: menu.querySelector(".popover-head")?.textContent };
      window.__result = { placeholder: input.placeholder, at, slash };`);
    expect(out).toMatchObject({
      ok: true,
      at: { hidden: false, head: "References · files, folders, selection" },
      slash: { hidden: false, head: "Commands" },
    });
    expect(out.placeholder).toMatch(/@ and \/ buttons/);
  });

  it("keeps the old @ rows (dimmed) while a new search is pending, and never accepts them with Enter, Tab or a click", () => {
    const out = run(`${richState}
      const input = document.querySelector("textarea");
      const menu = document.getElementById("composer-menu");
      const chips = document.querySelector(".composer-chips");
      const lastQuery = () => [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      const rows = () => [...menu.querySelectorAll("[role='option'] .popover-title")].map((n) => n.textContent);
      const typeText = (text) => {
        input.value = text;
        input.setSelectionRange(text.length, text.length);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      };
      const answer = (items) => {
        const q = lastQuery();
        host({ type: "mentionResults", requestId: q.requestId, query: q.query, items });
      };
      const file = (name) => ({ kind: "file", path: name, label: name, detail: "" });

      typeText("see @a");
      answer([file("alpha.ts"), file("beta.ts")]);
      const busy = () => document.getElementById("composer-listbox")?.getAttribute("aria-busy") ?? null;
      const settled = { rows: rows(), stale: menu.classList.contains("stale"), busy: busy() };

      typeText("see @al");
      const pending = { rows: rows(), stale: menu.classList.contains("stale"), busy: busy() };
      const tab = new KeyboardEvent("keydown", { key: "Tab", cancelable: true, bubbles: true });
      input.dispatchEvent(tab);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      menu.querySelectorAll("[role='option']")[0].click();
      const afterKeys = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
        tabPrevented: tab.defaultPrevented,
        sends: window.__posted.filter((m) => m.type === "send").length,
      };

      answer([file("alpha.ts")]);
      const fresh = { rows: rows(), stale: menu.classList.contains("stale"), busy: busy() };
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const accepted = { value: input.value, chipCount: chips.querySelectorAll(".chip").length };

      typeText("see @alpha.ts @b");
      answer([file("beta.ts")]);
      typeText("see @alpha.ts @be");
      setTimeout(() => {
        const afterTimeout = { rows: rows(), empty: menu.querySelector(".popover-empty")?.textContent };
        document.getElementById("result").textContent = JSON.stringify({
          ok: true, settled, pending, afterKeys, fresh, accepted, afterTimeout,
        });
      }, 1100);`);
    expect(out).toMatchObject({
      ok: true,
      settled: { rows: ["alpha.ts", "beta.ts"], stale: false, busy: null },
      pending: { rows: ["alpha.ts", "beta.ts"], stale: true, busy: "true" },
      afterKeys: { value: "see @al", chipCount: 0, tabPrevented: true, sends: 0 },
      fresh: { rows: ["alpha.ts"], stale: false, busy: null },
      accepted: { value: "see @alpha.ts ", chipCount: 1 },
      afterTimeout: { rows: [], empty: "No matching files or folders" },
    });
  });
});

describe.skipIf(!chrome || !built)("webview: P0 state restore, streaming tools, model picker, chip sync & busy commands", () => {
  const models = `[{ id: "gpt-4o", name: "GPT-4o" }, { id: "deepseek-chat", name: "DeepSeek Chat" }]`;
  const modes = `[{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }]`;

  it("restores draft, chips, and open <details> on initial reset and clears transient state on subsequent resets", () => {
    const preseed = `window.__webviewState = ${JSON.stringify({
      draft: "check @src/app.ts ",
      attachments: [{ kind: "file", path: "src/app.ts" }],
      followTail: false,
      scrollTop: 80,
      openDetails: { "thought:a1": true, "tool:t1": true },
    })};`;
    const out = run(
      `${state}
      state({});
      host({
        type: "reset",
        transcript: [
          { type: "agent", id: "a1", text: "done", thought: "plan" },
          {
            type: "tool",
            toolCall: {
              id: "t1",
              title: "bash",
              kind: "execute",
              status: "completed",
              terminal: { output: "long ".repeat(120), exitCode: 0 },
            },
          },
        ],
      });
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");
      const restored = {
        draft: input.value,
        chipsHidden: chips.hidden,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
        thoughtOpen: document.querySelector(".thought").open,
        toolOpen: document.querySelector(".tool-output").open,
        jumpHidden: document.querySelector(".jump-bottom").hidden,
      };
      host({ type: "reset", transcript: [] });
      window.__result = {
        restored,
        afterSecondReset: {
          chipsHidden: chips.hidden,
          chipCount: chips.querySelectorAll(".chip").length,
        },
      };`,
      preseed,
    );
    expect(out).toMatchObject({
      ok: true,
      restored: {
        draft: "check @src/app.ts ",
        chipsHidden: false,
        chipLabels: ["app.ts"],
        thoughtOpen: true,
        toolOpen: true,
        jumpHidden: false,
      },
      afterSecondReset: {
        chipsHidden: true,
        chipCount: 0,
      },
    });
  });

  it("updates streaming tool output in place without destroying <details>/<pre> or overriding user toggle", () => {
    const out = run(`${state}
      state({ status: "busy" });
      host({ type: "toolCall", toolCall: { id: "t1", title: "pytest", kind: "execute", status: "pending" } });
      host({ type: "toolCall", toolCall: { id: "t1", title: "pytest", kind: "execute", status: "in_progress", terminal: { output: "x".repeat(450) + "\\n" } } });
      const detailsEl = document.querySelector(".tool-output");
      const preEl = document.querySelector(".tool-output pre");
      const initiallyOpen = detailsEl.open;
      detailsEl.querySelector("summary").click();
      const afterUserClick = detailsEl.open;
      setTimeout(() => {
        host({ type: "toolCallOutput", id: "t1", delta: "chunk-a\\n", status: "in_progress" });
        host({ type: "toolCallOutput", id: "t1", delta: "chunk-b\\n", status: "in_progress" });
        host({ type: "toolCall", toolCall: { id: "t1", title: "pytest", kind: "execute", status: "completed", terminal: { output: "x".repeat(450) + "\\nchunk-a\\nchunk-b\\n", exitCode: 0 } } });
        document.getElementById("result").textContent = JSON.stringify({
          ok: true,
          initiallyOpen,
          afterUserClick,
          sameDetails: document.querySelector(".tool-output") === detailsEl,
          samePre: document.querySelector(".tool-output pre") === preEl,
          stillOpen: detailsEl.open,
          endsWith: preEl.textContent.slice(-16),
          status: document.querySelector(".tool").dataset.status,
        });
      }, 30);`);
    expect(out).toMatchObject({
      ok: true,
      initiallyOpen: false,
      afterUserClick: true,
      sameDetails: true,
      samePre: true,
      stillOpen: true,
      endsWith: "chunk-a\nchunk-b\n",
      status: "completed",
    });
  });

  it("renders a Model picker after Mode when multiple models exist and disables it when busy", () => {
    const out = run(`${state}
      state({ mode: { current: "ask", options: ${modes} }, models: { current: "gpt-4o", options: ${models} } });
      const firstSelect = document.querySelector("select");
      const modelSelect = document.querySelector(".model-picker select");
      const readyInfo = {
        firstIsMode: firstSelect !== modelSelect && firstSelect.value === "ask",
        options: [...modelSelect.options].map((o) => o.value),
        value: modelSelect.value,
        disabled: modelSelect.disabled,
      };
      modelSelect.value = "deepseek-chat";
      modelSelect.dispatchEvent(new Event("change"));
      state({ status: "busy", mode: { current: "ask", options: ${modes} }, models: { current: "deepseek-chat", options: ${models} } });
      window.__result = {
        readyInfo,
        busyDisabled: document.querySelector(".model-picker select").disabled,
        posted: window.__posted.filter((m) => m.type === "setModel"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      readyInfo: {
        firstIsMode: true,
        options: ["gpt-4o", "deepseek-chat"],
        value: "gpt-4o",
        disabled: false,
      },
      busyDisabled: true,
      posted: [{ type: "setModel", value: "deepseek-chat" }],
    });
  });

  it("removes attached @ chips automatically when the inline token is deleted manually", () => {
    const out = run(`${state}
      state({});
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");
      input.value = "check @app";
      input.setSelectionRange(10, 10);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const req = [...window.__posted].filter((m) => m.type === "queryMentions").at(-1);
      host({
        type: "mentionResults",
        requestId: req.requestId,
        query: req.query,
        items: [{ kind: "file", path: "src/app.ts", label: "app.ts", detail: "src" }],
      });
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const countBefore = chips.querySelectorAll(".chip").length;

      input.value = "check ";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const countAfter = chips.querySelectorAll(".chip").length;
      const hiddenAfter = chips.hidden;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = {
        countBefore,
        countAfter,
        hiddenAfter,
        sends: window.__posted.filter((m) => m.type === "send"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      countBefore: 1,
      countAfter: 0,
      hiddenAfter: true,
      sends: [{ type: "send", text: "check " }],
    });
  });

  it("collapses streamed output automatically once it grows to >= 400 chars unless user-toggled, and opens on failure", () => {
    const out = run(`${state}
      state({ status: "busy" });
      host({ type: "toolCall", toolCall: { id: "t1", title: "bash", kind: "execute", status: "in_progress", terminal: { output: "line 1\\n" } } });
      const detailsEl = document.querySelector(".tool-output");
      const openInitially = detailsEl.open;
      host({ type: "toolCallOutput", id: "t1", delta: "y".repeat(420), status: "in_progress" });
      const openAfterGrowth = detailsEl.open;
      host({ type: "toolCall", toolCall: { id: "t1", title: "bash", kind: "execute", status: "failed", terminal: { output: "line 1\\n" + "y".repeat(420), exitCode: 1 } } });
      const openAfterFailure = detailsEl.open;
      window.__result = { openInitially, openAfterGrowth, openAfterFailure };`);
    expect(out).toMatchObject({
      ok: true,
      openInitially: true,
      openAfterGrowth: false,
      openAfterFailure: true,
    });
  });

  it("allows /log while busy and explains why /new must wait without wiping the input", () => {
    const out = run(`${state}
      state({ status: "busy" });
      const input = document.querySelector("textarea");
      input.value = "/log";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterLog = { value: input.value, posted: window.__posted.slice(1) };

      input.value = "/new";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const notices = [...document.querySelectorAll(".notice")].map((n) => n.textContent);
      window.__result = {
        afterLog,
        afterNewValue: input.value,
        newPosted: window.__posted.some((m) => m.type === "newChat"),
        notices,
      };`);
      expect(out).toMatchObject({
      ok: true,
      afterLog: { value: "", posted: [{ type: "openLog" }] },
      afterNewValue: "/new",
      newPosted: false,
      notices: ["Wait until the current action finishes before running this command."],
    });
  });

  it("throttles rapid agentThought and toolCallOutput updates and flushes them immediately on turnEnd", () => {
    const out = run(`${state}
      state({ status: "busy", showThinking: true });
      host({ type: "agentThought", messageId: "a1", text: "step 1" });
      host({ type: "toolCall", toolCall: { id: "t1", title: "bash", kind: "execute", status: "in_progress", terminal: { output: "line 1\\n" } } });
      const thoughtEl = document.querySelector(".thought-body");
      const toolPre = document.querySelector(".tool-output pre");
      const afterFirst = {
        thought: thoughtEl.textContent,
        tool: toolPre.textContent,
      };

      // Immediate second delta within the 80ms throttle window stays buffered before turnEnd.
      host({ type: "agentThought", messageId: "a1", text: " + step 2" });
      host({ type: "toolCallOutput", id: "t1", delta: "line 2\\n", status: "in_progress" });
      const whileThrottled = {
        thought: thoughtEl.textContent,
        tool: toolPre.textContent,
      };

      // turnEnd cancels pending timers and flushes both immediately.
      host({ type: "turnEnd", stopReason: "end_turn" });
      const afterTurnEnd = {
        thought: thoughtEl.textContent,
        tool: toolPre.textContent,
      };
      window.__result = { afterFirst, whileThrottled, afterTurnEnd };`);
      expect(out).toMatchObject({
      ok: true,
      afterFirst: { thought: "step 1", tool: "line 1\n" },
      whileThrottled: { thought: "step 1", tool: "line 1\n" },
      afterTurnEnd: { thought: "step 1 + step 2", tool: "line 1\nline 2\n" },
    });
  });
});

describe.skipIf(!chrome || !built)("webview: P1 images, insertMention, saveAll, history, and Escape cancel", () => {
  it("handles insertMention, saveAll notice action, prompt history recall, and Escape to cancel when busy", () => {
    const out = run(`${state}
      state({});
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");

      // 1. insertMention inserts token at cursor and adds chip.
      input.value = "inspect";
      input.setSelectionRange(7, 7);
      host({ type: "insertMention", item: { kind: "selection", path: "src/app.ts", range: { startLine: 10, endLine: 20 } } });
      const afterInsert = {
        value: input.value,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
      };

      // Send first prompt, then second prompt.
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      input.value = "second prompt";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

      // 2. Type a draft, press ArrowUp twice to recall history, then ArrowDown twice to restore draft.
      input.value = "unsent draft";
      input.setSelectionRange(0, 0);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const up1 = input.value;
      input.setSelectionRange(0, 0);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const up2 = input.value;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      const down1 = input.value;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      const down2 = input.value;

      // 3. saveAll notice action posts saveAll.
      host({ type: "notice", level: "info", text: "1 file has unsaved changes.", actions: [{ id: "saveAll", label: "Save all" }] });
      document.querySelector(".notice button").click();

      // 4. Escape while busy and menu closed posts cancel.
      state({ status: "busy" });
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

      window.__result = {
        afterInsert,
        up1,
        up2,
        down1,
        down2,
        saveAllPosted: window.__posted.some((m) => m.type === "saveAll"),
        cancelPosted: window.__posted.some((m) => m.type === "cancel"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      afterInsert: {
        value: "inspect @src/app.ts:L10-L20 ",
        chipLabels: ["app.ts:L10-L20"],
      },
      up1: "second prompt",
      up2: "inspect @src/app.ts:L10-L20 ",
      down1: "second prompt",
      down2: "unsent draft",
      saveAllPosted: true,
      cancelPosted: true,
    });
  });

  it("attaches pasted and dropped images as chips, supports removing an image chip, and submits image-only prompts", () => {
    const out = run(
      `${state}
      state({});
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");

      // Simulate image paste on input.
      const pasteEvent = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(pasteEvent, "clipboardData", {
        value: {
          items: [
            {
              type: "image/png",
              getAsFile: () => ({ type: "image/png", name: "clip.png", __base64: "iVBORw0KGgo=" }),
            },
            {
              type: "image/jpeg",
              getAsFile: () => ({ type: "image/jpeg", name: "photo.jpg", __base64: "AAAA" }),
            },
          ],
        },
      });
      input.dispatchEvent(pasteEvent);

      const chipLabelsBefore = [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent);

      // Remove first image chip.
      chips.querySelector(".image-chip .chip-remove").click();
      const chipLabelsAfterRemove = [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent);

      // Submit with empty text and remaining image attachment.
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const sends = window.__posted.filter((m) => m.type === "send");
      window.__result = {
        chipLabelsBefore,
        chipLabelsAfterRemove,
        chipsHiddenAfterSend: chips.hidden,
        sends,
      };`,
      `window.FileReader = class {
        constructor() { this._listeners = []; this.result = ""; }
        addEventListener(type, fn) { if (type === "load") this._listeners.push(fn); }
        readAsDataURL(file) {
          this.result = "data:" + file.type + ";base64," + (file.__base64 || "iVBORw0KGgo=");
          for (const fn of this._listeners) fn();
        }
      };`,
    );
    expect(out).toMatchObject({
      ok: true,
      chipLabelsBefore: ["[Image: clip.png]", "[Image: photo.jpg]"],
      chipLabelsAfterRemove: ["[Image: photo.jpg]"],
      chipsHiddenAfterSend: true,
      sends: [
        {
          type: "send",
          text: "",
          images: [{ mimeType: "image/jpeg", data: "AAAA", name: "photo.jpg" }],
        },
      ],
    });
  });

  it("tokenizes code blocks into tok-kw, tok-str, tok-num, and tok-com spans without altering textContent and posts copy to host", () => {
    const code = 'const count = 42; // answer\nconst label = "hi";';
    const out = run(`${mockClipboard}
      ${state}
      state({});
      host({ type: "agentText", messageId: "a", text: ${lit("```ts\n" + code + "\n```")} });
      const codeEl = document.querySelector("pre.code code");
      const kws = [...codeEl.querySelectorAll(".tok-kw")].map((n) => n.textContent);
      const nums = [...codeEl.querySelectorAll(".tok-num")].map((n) => n.textContent);
      const strs = [...codeEl.querySelectorAll(".tok-str")].map((n) => n.textContent);
      const coms = [...codeEl.querySelectorAll(".tok-com")].map((n) => n.textContent);
      document.querySelector(".code-copy").click();
      window.__result = {
        fullText: codeEl.textContent,
        kws,
        nums,
        strs,
        coms,
        copyPosted: window.__posted.filter((m) => m.type === "copy"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      fullText: code,
      kws: ["const", "const"],
      nums: ["42"],
      strs: ['"hi"'],
      coms: ["// answer"],
      copyPosted: [{ type: "copy", text: code }],
    });
  });

  it("posts openFile when clicking a file or selection @ chip label and a tool card Open button", () => {
    const out = run(`${state}
      state({ cwd: "/workspace/proj" });
      host({ type: "insertMention", item: { kind: "selection", path: "src/app.ts", range: { startLine: 10, endLine: 20 } } });
      host({ type: "insertMention", item: { kind: "file", path: "src/util.ts" } });
      host({ type: "insertMention", item: { kind: "folder", path: "src/" } });

      const chips = [...document.querySelectorAll(".composer-chips .chip")];
      const selectionLabel = chips[0].querySelector(".chip-label");
      const fileLabel = chips[1].querySelector(".chip-label");
      const folderLabel = chips[2].querySelector(".chip-label");

      const chipMeta = {
        selectionRole: selectionLabel.getAttribute("role"),
        selectionTabIndex: selectionLabel.tabIndex,
        fileRole: fileLabel.getAttribute("role"),
        folderRole: folderLabel.getAttribute("role"),
      };

      selectionLabel.click();
      fileLabel.click();
      folderLabel.click();
      // Removing the selection chip must not fire another openFile message.
      chips[0].querySelector(".chip-remove").click();

      host({ type: "toolCall", toolCall: { id: "t-open", title: "read src/app.ts", kind: "read", status: "completed", rawInput: { path: "./src\\\\app.ts" } } });
      host({ type: "toolCall", toolCall: { id: "t-unsafe", title: "read ../secret.txt", kind: "read", status: "completed", rawInput: { path: "../secret.txt" } } });
      host({ type: "toolCall", toolCall: { id: "t-both", title: "edit src/app.ts", kind: "edit", status: "completed", rawInput: { path: "src/app.ts" }, canDiff: true } });
      host({ type: "toolCall", toolCall: { id: "t-abs-in", title: "read /workspace/proj/src/abs.ts", kind: "read", status: "completed", rawInput: { path: "/workspace/proj/src/abs.ts" } } });
      host({ type: "toolCall", toolCall: { id: "t-abs-out", title: "read /other/proj/src/abs.ts", kind: "read", status: "completed", rawInput: { path: "/other/proj/src/abs.ts" } } });
      host({ type: "toolCall", toolCall: { id: "t-abs-sibling", title: "read /workspace/proj-2/src/abs.ts", kind: "read", status: "completed", rawInput: { path: "/workspace/proj-2/src/abs.ts" } } });

      // Windows drive-letter case-insensitive prefix matching.
      state({ cwd: "C:\\\\Workspace\\\\Proj" });
      host({ type: "toolCall", toolCall: { id: "t-win-in", title: "read win", kind: "read", status: "completed", rawInput: { path: "c:\\\\workspace\\\\proj\\\\src\\\\win.ts" } } });
      host({ type: "toolCall", toolCall: { id: "t-win-out", title: "read win out", kind: "read", status: "completed", rawInput: { path: "D:\\\\Workspace\\\\Proj\\\\src\\\\win.ts" } } });

      const openButtons = [...document.querySelectorAll(".tool-open-btn")];
      openButtons[0].click();
      openButtons[2].click();
      openButtons[3].click();

      const bothButtons = [...document.querySelectorAll(".tool")][2].querySelectorAll(".actions button");
      window.__result = {
        chipMeta,
        remainingChips: document.querySelectorAll(".composer-chips .chip").length,
        openButtonCount: openButtons.length,
        bothButtonLabels: [...bothButtons].map((b) => b.textContent),
        openFiles: window.__posted.filter((m) => m.type === "openFile"),
      };`);
    expect(out).toMatchObject({
      ok: true,
      chipMeta: {
        selectionRole: "button",
        selectionTabIndex: 0,
        fileRole: "button",
        folderRole: null,
      },
      remainingChips: 2,
      openButtonCount: 4,
      bothButtonLabels: ["Open", "Show diff"],
      openFiles: [
        { type: "openFile", path: "src/app.ts", line: 10 },
        { type: "openFile", path: "src/util.ts" },
        { type: "openFile", path: "src/app.ts" },
        { type: "openFile", path: "src/abs.ts" },
        { type: "openFile", path: "src/win.ts" },
      ],
    });
  });

  it("refuses pasted and dropped images with a warning notice when supportsImages is false", () => {
    const out = run(
      `${state}
      state({ supportsImages: false });
      const input = document.querySelector("textarea");
      const composer = document.querySelector(".composer");
      const chips = document.querySelector(".composer-chips");

      const pasteEvent = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(pasteEvent, "clipboardData", {
        value: {
          items: [
            {
              type: "image/png",
              getAsFile: () => ({ type: "image/png", name: "clip.png", __base64: "iVBORw0KGgo=" }),
            },
          ],
        },
      });
      input.dispatchEvent(pasteEvent);

      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", {
        value: {
          files: [{ type: "image/jpeg", name: "drop.jpg", __base64: "AAAA" }],
        },
      });
      composer.dispatchEvent(dropEvent);

      const notices = [...document.querySelectorAll(".notice.warning")].map((n) => n.textContent);
      window.__result = {
        pastePrevented: pasteEvent.defaultPrevented,
        dropPrevented: dropEvent.defaultPrevented,
        chipCount: chips.querySelectorAll(".chip").length,
        chipsHidden: chips.hidden,
        notices,
        sends: window.__posted.filter((m) => m.type === "send"),
      };`,
      `window.FileReader = class {
        constructor() { this._listeners = []; this.result = ""; }
        addEventListener(type, fn) { if (type === "load") this._listeners.push(fn); }
        readAsDataURL(file) {
          this.result = "data:" + file.type + ";base64," + (file.__base64 || "iVBORw0KGgo=");
          for (const fn of this._listeners) fn();
        }
      };`,
    );
    expect(out).toMatchObject({
      ok: true,
      pastePrevented: true,
      dropPrevented: true,
      chipCount: 0,
      chipsHidden: true,
      notices: [
        "This agent does not support image attachments.",
        "This agent does not support image attachments.",
      ],
      sends: [],
    });
  });

  it("preserves drafts and @ chips across ArrowUp/ArrowDown/Escape history navigation and guards mid-draft ArrowUp", () => {
    const out = run(`${state}
      state({});
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");

      // Send two prompts into history.
      input.value = "first prompt";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      input.value = "second prompt";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

      // 1. ArrowUp on empty box recalls latest history entry.
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const recalledFromEmpty = input.value;
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      const restoredEmpty = input.value;

      // 2. ArrowUp with cursor in the middle of a draft (pos 2) must NOT overwrite the draft.
      input.value = "my long draft G";
      input.setSelectionRange(2, 2);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const afterMidDraftUp = input.value;

      // 3. Build a draft with an attached @ chip, recall at cursor 0, edit recalled text,
      // verify ArrowUp and ArrowDown do not clobber the edit, then Escape restores draft + chip.
      input.value = "check";
      input.setSelectionRange(5, 5);
      host({ type: "insertMention", item: { kind: "file", path: "src/app.ts" } });
      const originalDraft = input.value;
      input.setSelectionRange(0, 0);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const afterRecall = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
      };
      input.value = "second prompt edited";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.setSelectionRange(0, 0);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      const afterEditedUp = input.value;
      input.setSelectionRange(input.value.length, input.value.length);
      const downEvent = new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true });
      input.dispatchEvent(downEvent);
      const afterEditedDown = {
        value: input.value,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
        defaultPrevented: downEvent.defaultPrevented,
      };
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const afterEditedEscape = {
        value: input.value,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
      };

      // 4. Recall again, edit, then press Escape while ready to restore draft + chip.
      input.setSelectionRange(0, 0);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      input.value = "another edit";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const afterEscape = {
        value: input.value,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
      };

      window.__result = {
        recalledFromEmpty,
        restoredEmpty,
        afterMidDraftUp,
        originalDraft,
        afterRecall,
        afterEditedUp,
        afterEditedDown,
        afterEditedEscape,
        afterEscape,
      };`);
    expect(out).toMatchObject({
      ok: true,
      recalledFromEmpty: "second prompt",
      restoredEmpty: "",
      afterMidDraftUp: "my long draft G",
      originalDraft: "check @src/app.ts ",
      afterRecall: { value: "second prompt", chipCount: 0 },
      afterEditedUp: "second prompt edited",
      afterEditedDown: { value: "second prompt edited", chipLabels: [], defaultPrevented: false },
      afterEditedEscape: { value: "check @src/app.ts ", chipLabels: ["app.ts"] },
      afterEscape: { value: "check @src/app.ts ", chipLabels: ["app.ts"] },
    });
  });

  it("preserves pasted images across non-/new client commands and restores sanitized images from webview state", () => {
    const mockFileReader = `window.FileReader = class {
      constructor() { this._listeners = []; this.result = ""; }
      addEventListener(type, fn) { if (type === "load") this._listeners.push(fn); }
      readAsDataURL(file) {
        this.result = "data:" + file.type + ";base64," + (file.__base64 || "iVBORw0KGgo=");
        for (const fn of this._listeners) fn();
      }
    };`;
    const out = run(
      `${state}
      state({ mode: { current: "ask", options: [{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }] } });
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");

      // Paste an image and verify state persistence.
      const pasteEvent = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(pasteEvent, "clipboardData", {
        value: {
          items: [
            {
              type: "image/png",
              getAsFile: () => ({ type: "image/png", name: "clip.png", __base64: "iVBORw0KGgo=" }),
            },
          ],
        },
      });
      input.dispatchEvent(pasteEvent);
      const persistedAfterPaste = window.__webviewState?.images;

      // Typing /log + Enter must preserve attached images.
      input.value = "/log";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterLog = {
        value: input.value,
        chipLabels: [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent),
        persistedImages: window.__webviewState?.images,
        openLogCount: window.__posted.filter((m) => m.type === "openLog").length,
      };

      // Typing /new + Enter clears attached images.
      input.value = "/new";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const afterNew = {
        value: input.value,
        chipCount: chips.querySelectorAll(".chip").length,
        persistedImages: window.__webviewState?.images,
        newChatCount: window.__posted.filter((m) => m.type === "newChat").length,
      };

      window.__result = { persistedAfterPaste, afterLog, afterNew };`,
      mockFileReader,
    );
    expect(out).toMatchObject({
      ok: true,
      persistedAfterPaste: [{ mimeType: "image/png", data: "iVBORw0KGgo=", name: "clip.png" }],
      afterLog: {
        value: "",
        chipLabels: ["[Image: clip.png]"],
        persistedImages: [{ mimeType: "image/png", data: "iVBORw0KGgo=", name: "clip.png" }],
        openLogCount: 1,
      },
      afterNew: {
        value: "",
        chipCount: 0,
        persistedImages: [],
        newChatCount: 1,
      },
    });

    // Reloading with preseeded window.__webviewState.images restores valid images and filters invalid ones.
    const preseed = `window.__webviewState = ${JSON.stringify({
      draft: "look at this",
      attachments: [],
      images: [
        { mimeType: "image/png", data: "iVBORw0KGgo=", name: "saved.png" },
        { mimeType: "image/svg+xml", data: "AAAA", name: "bad.svg" },
        { mimeType: "image/jpeg", data: "not base64!!!", name: "bad.jpg" },
      ],
      followTail: true,
      scrollTop: 0,
      openDetails: {},
    })};`;
    const reloaded = run(
      `${state}
      state({});
      const input = document.querySelector("textarea");
      const chips = document.querySelector(".composer-chips");
      const restoredLabels = [...chips.querySelectorAll(".chip-label")].map((c) => c.textContent);
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = {
        restoredLabels,
        sends: window.__posted.filter((m) => m.type === "send"),
      };`,
      preseed,
    );
    expect(reloaded).toMatchObject({
      ok: true,
      restoredLabels: ["[Image: saved.png]"],
      sends: [
        {
          type: "send",
          text: "look at this",
          images: [{ mimeType: "image/png", data: "iVBORw0KGgo=", name: "saved.png" }],
        },
      ],
    });
  });

  it("keeps large images out of the saved state, tells the user, and still sends them", () => {
    const mockFileReader = `window.FileReader = class {
      constructor() { this._listeners = []; this.result = ""; }
      addEventListener(type, fn) { if (type === "load") this._listeners.push(fn); }
      readAsDataURL(file) {
        this.result = "data:" + file.type + ";base64," + file.__base64;
        for (const fn of this._listeners) fn();
      }
    };`;
    const out = run(
      `${state}
      state({});
      const input = document.querySelector("textarea");
      const paste = (name, chars) => {
        const ev = new Event("paste", { bubbles: true, cancelable: true });
        Object.defineProperty(ev, "clipboardData", { value: { items: [{ type: "image/png", getAsFile: () => ({ type: "image/png", name, __base64: "A".repeat(chars) }) }] } });
        input.dispatchEvent(ev);
      };
      const notices = () => [...document.querySelectorAll(".notice")].map((n) => n.textContent);
      paste("small.png", 1_200_000);
      const afterSmall = { saved: window.__webviewState.images.map((i) => i.name), notices: notices().length };
      paste("big.png", 1_200_000);
      const afterBig = { saved: window.__webviewState.images.map((i) => i.name), notices: notices().length, size: JSON.stringify(window.__webviewState).length };
      input.value = "typing goes on";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.value = "typing goes on";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const sent = window.__posted.filter((m) => m.type === "send");
      window.__result = { afterSmall, afterBig, sentImages: sent[0].images.map((i) => i.name) };`,
      mockFileReader,
    );
    expect(out).toMatchObject({
      ok: true,
      afterSmall: { saved: ["small.png"], notices: 0 },
      afterBig: { saved: ["small.png"], notices: 1 },
      sentImages: ["small.png", "big.png"],
    });
    expect((out.afterBig as { size: number }).size).toBeLessThan(2_100_000);
  });

  it("saves three of four images (1.5M, 1.5M, 0.1M, 0.1M), shows one notice, and still sends all four", () => {
    const mockFileReader = `window.FileReader = class {
      constructor() { this._listeners = []; this.result = ""; }
      addEventListener(type, fn) { if (type === "load") this._listeners.push(fn); }
      readAsDataURL(file) {
        this.result = "data:" + file.type + ";base64," + file.__base64;
        for (const fn of this._listeners) fn();
      }
    };`;
    const out = run(
      `${state}
      state({});
      const input = document.querySelector("textarea");
      for (const [name, chars] of [["a.png", 1_500_000], ["b.png", 1_500_000], ["c.png", 100_000], ["d.png", 100_000]]) {
        const ev = new Event("paste", { bubbles: true, cancelable: true });
        Object.defineProperty(ev, "clipboardData", { value: { items: [{ type: "image/png", getAsFile: () => ({ type: "image/png", name, __base64: "A".repeat(chars) }) }] } });
        input.dispatchEvent(ev);
      }
      const saved = window.__webviewState.images.map((i) => i.name);
      const notices = [...document.querySelectorAll(".notice")].filter((n) => n.textContent.includes("Large images")).length;
      input.value = "go";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      window.__result = { saved, notices, sentImages: window.__posted.filter((m) => m.type === "send")[0].images.map((i) => i.name) };`,
      mockFileReader,
    );
    expect(out).toMatchObject({
      ok: true,
      saved: ["a.png", "c.png", "d.png"],
      notices: 1,
      sentImages: ["a.png", "b.png", "c.png", "d.png"],
    });
  });

  it("keeps the small images after a large one, in order, shows one notice, and drops the notice when all fit", () => {
    const mockFileReader = `window.FileReader = class {
      constructor() { this._listeners = []; this.result = ""; }
      addEventListener(type, fn) { if (type === "load") this._listeners.push(fn); }
      readAsDataURL(file) {
        this.result = "data:" + file.type + ";base64," + file.__base64;
        for (const fn of this._listeners) fn();
      }
    };`;
    const out = run(
      `${state}
      state({});
      const input = document.querySelector("textarea");
      const paste = (name, chars) => {
        const ev = new Event("paste", { bubbles: true, cancelable: true });
        Object.defineProperty(ev, "clipboardData", { value: { items: [{ type: "image/png", getAsFile: () => ({ type: "image/png", name, __base64: "A".repeat(chars) }) }] } });
        input.dispatchEvent(ev);
      };
      const notices = () => [...document.querySelectorAll(".notice")].filter((n) => n.textContent.includes("Large images")).length;
      paste("a.png", 1_500_000);
      paste("b.png", 1_500_000);
      paste("c.png", 100_000);
      paste("d.png", 100_000);
      const afterPaste = { saved: window.__webviewState.images.map((i) => i.name), notices: notices(), size: JSON.stringify(window.__webviewState).length };
      document.querySelectorAll(".composer-chips .image-chip .chip-remove")[1].click();
      const afterRemove = { saved: window.__webviewState.images.map((i) => i.name), notices: notices() };
      paste("b2.png", 1_500_000);
      const afterBigAgain = { saved: window.__webviewState.images.map((i) => i.name), notices: notices() };
      input.value = "go";
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      const sent = window.__posted.filter((m) => m.type === "send");
      window.__result = { afterPaste, afterRemove, afterBigAgain, sentImages: sent[0].images.map((i) => i.name) };`,
      mockFileReader,
    );
    expect(out).toMatchObject({
      ok: true,
      afterPaste: { saved: ["a.png", "c.png", "d.png"], notices: 1 },
      afterRemove: { saved: ["a.png", "c.png", "d.png"], notices: 0 },
      afterBigAgain: { saved: ["a.png", "c.png", "d.png"], notices: 1 },
      sentImages: ["a.png", "c.png", "d.png", "b2.png"],
    });
    expect((out.afterPaste as { size: number }).size).toBeLessThan(2_100_000);
  });
});
