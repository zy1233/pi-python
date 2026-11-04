// Builds an HTML page that runs dist/webview.js outside VS Code: `acquireVsCodeApi` is a mock that
// records what the page posts, and a script feeds it host messages and clicks its buttons.
// Used by test/webview.test.ts (assertions) and by hand for screenshots:
//   node scripts/webview-harness.mjs out.html [demo]
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const DARK_THEME = `
:root {
  --vscode-font-family: -apple-system, "Segoe UI", sans-serif; --vscode-font-size: 13px;
  --vscode-foreground: #cccccc; --vscode-sideBar-background: #181818; --vscode-editor-background: #1f1f1f;
  --vscode-descriptionForeground: #9d9d9d; --vscode-focusBorder: #0078d4; --vscode-widget-border: #454545;
  --vscode-panel-border: #2b2b2b; --vscode-button-background: #0078d4; --vscode-button-foreground: #ffffff;
  --vscode-button-hoverBackground: #026ec1; --vscode-button-secondaryBackground: #313131;
  --vscode-button-secondaryForeground: #cccccc; --vscode-button-secondaryHoverBackground: #3c3c3c;
  --vscode-input-background: #313131; --vscode-input-foreground: #cccccc; --vscode-input-border: #3c3c3c;
  --vscode-dropdown-background: #313131; --vscode-dropdown-foreground: #cccccc; --vscode-dropdown-border: #3c3c3c;
  --vscode-textBlockQuote-background: #2b2b2b; --vscode-textCodeBlock-background: #2b2b2b;
  --vscode-textLink-foreground: #4daafc; --vscode-editor-font-family: Menlo, Consolas, monospace;
  --vscode-badge-background: #616161; --vscode-badge-foreground: #ffffff;
  --vscode-testing-iconPassed: #73c991; --vscode-editorWarning-foreground: #cca700;
  --vscode-editorError-foreground: #f14c4c; --vscode-errorForeground: #f85149;
}`;

export function buildHarness({ script, demo }) {
  const cssHref = "file://" + path.join(root, "media", "chat.css");
  const jsSrc = "file://" + path.join(root, "dist", "webview.js");
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${DARK_THEME}</style>
<link rel="stylesheet" href="${cssHref}"></head><body><div id="root"></div>${demo ? "" : '<pre id="result"></pre>'}
<script>
window.__posted = [];
window.acquireVsCodeApi = () => ({ postMessage: (m) => window.__posted.push(JSON.parse(JSON.stringify(m))) });
window.host = (m) => window.dispatchEvent(new MessageEvent("message", { data: m }));
</script>
<script src="${jsSrc}"></script>
<script>${script}</script>
</body></html>`;
}

export const DEMO_SCRIPT = `
const state = (o) => host({ type: "state", status: "ready", canRestart: true, agent: { version: "0.8.0", source: "bundled" }, model: "deepseek-chat", mode: { current: "ask", options: [{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }] }, ...o });
state({});
host({ type: "userMessage", id: "u1", text: "Add a hello.txt file and show me the date" });
host({ type: "agentThought", messageId: "a1", text: "I should write the file, then run date." });
host({ type: "agentText", messageId: "a1", text: "I will create \`hello.txt\` first, then run \`date\`.\\n\\n- write the file\\n- run the command\\n\\nSee [the docs](https://zycode.dev/docs)." });
host({ type: "toolCall", toolCall: { id: "t1", title: "write hello.txt", kind: "edit", status: "pending", rawInput: { path: "hello.txt" } } });
host({ type: "permission", requestId: "p1", kind: "tool", toolCallId: "t1", title: "write hello.txt", summary: "hello.txt", canDiff: false, options: [{ id: "allow-once", name: "Allow once", kind: "allow_once" }, { id: "reject-once", name: "Reject", kind: "reject_once" }] });
host({ type: "permissionDone", requestId: "p1", outcome: "selected", optionId: "allow-once" });
host({ type: "toolCall", toolCall: { id: "t1", title: "write hello.txt", kind: "edit", status: "completed", text: "Successfully wrote 6 bytes to hello.txt", canDiff: true } });
host({ type: "toolCall", toolCall: { id: "t2", title: "date", kind: "execute", status: "completed", rawInput: { command: "date" }, terminal: { output: "Wed Nov  4 07:30:00 UTC 2026\\n", exitCode: 0 } } });
host({ type: "permission", requestId: "p2", kind: "tool", toolCallId: "t3", title: "edit src/app.ts", summary: "src/app.ts", canDiff: true, options: [{ id: "allow-once", name: "Allow once", kind: "allow_once" }, { id: "reject-once", name: "Reject", kind: "reject_once" }] });
host({ type: "notice", level: "info", text: "2 files have unsaved changes. The agent reads the files on disk." });
state({ status: "busy" });
`;

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const out = process.argv[2] ?? "webview-harness.html";
  writeFileSync(out, buildHarness({ script: DEMO_SCRIPT, demo: true }));
  console.log(out);
}
