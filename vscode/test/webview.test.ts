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
