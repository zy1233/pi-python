# zycode VS Code extension: optimization plan

> Date: 2026-11-19.
> Scope: `vscode/` (extension `1.6.0`, post-`51b054c`), with optional future coordination in `packages/pi-agent-cli/` (bundled agent `1.0.0`).
> Status: **P0 implemented & verified (2026-11-19); P1–P3 recorded as roadmap.**
> Related: `docs/specs/2026-11-04-vscode-extension-design.md`, `docs/specs/2026-11-05-acp-extension-registry.md`, `docs/specs/2026-11-13-agent-context-usage.md`, `docs/specs/2026-11-16-thinking-max-and-turn-limit.md`.

---

## 0. Context & Architectural Invariants

The `vscode/` extension already has a clean three-layer split (`src/core/` with zero `vscode` imports, `src/ui/` for VS Code host integration, `src/webview/` for DOM rendering with zero `innerHTML`), strict workspace/project trust separation, and OIDC Trusted Publishing to Open VSX and the Marketplace.

Every optimization below preserves the core design invariants:

1. **`core/` stays free of `vscode` imports** (`eslint.config.mjs` enforces `no-restricted-imports`), so controller/state/protocol logic runs directly under Vitest.
2. **Webview remains untrusted** (`parseWebviewMessage` validates every host-bound message; `dom.ts` builds nodes via `textContent` and never uses `innerHTML`; CSP forbids remote assets).
3. **Never auto-answer permissions or trust prompts** (Design D4): dismissing a fallback dialog leaves the request pending rather than silently allowing or rejecting.
4. **Security-sensitive settings remain `machine` / `application` scoped** (Design D6): `zycode.agentPath`, `zycode.agentArgs`, `zycode.permissionMode` (and any future MCP config) never read workspace-local `.vscode/settings.json`.

---

## 1. Summary Matrix

| Priority | Theme | Target Scope | Primary Files |
|---|---|---|---|
| **P0-1** | Hidden-view permission approval fallback & status bar alert | Pure extension | `src/core/permission-fallback.ts`, `src/core/status-row.ts`, `src/core/chat-controller.ts`, `src/ui/permission-fallback.ts`, `src/ui/chat-view.ts`, `src/ui/status-bar.ts` |
| **P0-2** | Webview state preservation across sidebar switches | Pure extension | `src/extension.ts`, `src/webview/main.ts`, `scripts/webview-harness.mjs` |
| **P0-3** | Eliminate $O(N^2)$ tool output IPC duplication & DOM thrashing | Pure extension | `src/core/tool-calls.ts`, `src/core/protocol.ts`, `src/core/chat-controller.ts`, `src/webview/main.ts` |
| **P0-4** | Fix commit `51b054c` edge cases (`@` / `/` / model / watcher) | Pure extension | `src/core/mentions.ts`, `src/core/mention-excludes.ts`, `src/core/composer.ts`, `src/core/chat-controller.ts`, `src/ui/workspace-mentions.ts`, `src/webview/main.ts` |
| **P1** | Unlock agent images/MCP, editor deep integration, multi-root & composer UX | Pure extension | `package.json`, `src/core/acp-connection.ts`, `src/ui/vscode-host.ts`, `src/webview/main.ts` |
| **P2** | Narrow sidebar layout polish, syntax highlighting, host clipboard, usage discoverability | Pure extension | `package.json`, `src/webview/dom.ts`, `src/webview/main.ts`, `src/ui/sessions-view.ts`, `media/chat.css` |
| **P3** | Windows robustness, agent-coordinated live thinking & diffs, real host CI & distribution | Extension + Agent | `src/ui/diff-documents.ts`, `src/core/agent-process.ts`, `packages/pi-agent-cli/pi_agent_cli/{agent,events}.py`, `.github/workflows/vscode-extension.yml` |

---

## 2. P0 — Reliability & Current-UX Defects (Immediate Fix)

### P0-1. Hidden-View Permission Approval Fallback & Status Bar Indicator
- **Problem:** Design doc section 5.1 specifies a modal `window.showWarningMessage` fallback when `session/request_permission` arrives while the chat view is not visible, and warns that an unseen prompt leaves the agent blocked forever. Currently `ChatViewProvider` (`vscode/src/ui/chat-view.ts`) only calls `this.view?.webview.postMessage(message)` without inspecting `this.view?.visible` or showing a fallback dialog, and `StatusBar` (`vscode/src/ui/status-bar.ts`) continues showing a spinning `zycode: busy`.
- **Solution:**
  1. Add `pendingPermissions?: number` to `StateMessage` (`src/core/protocol.ts`), updated in `ChatController` (`onPermissionRequest`, `settlePermission`) so status bar and view stay synchronized.
  2. Create pure `src/core/permission-fallback.ts` (`buildPermissionFallback`, `resolveFallbackChoice`) formatting the tool/trust title, full command or path summary (never truncating `bash` commands), diff availability note, exact option list in agent order (no invented `always` option), plus an `Open chat` action. Dismissing (`undefined`) maps to `{ kind: "dismiss" }` without cancelling the request.
  3. Create `src/ui/permission-fallback.ts` (`PermissionFallback`), invoke it from `ChatViewProvider` whenever `message.type === "permission"` and `!this.view?.visible`, and clear its active tracking on `permissionDone`.
  4. Update `StatusBar.update()` using `approvalStatusText` / `approvalTooltipLine` in `src/core/status-row.ts` to show `$(warning) zycode: approval needed` (or `approval needed (N)`) with `statusBarItem.warningBackground` while waiting for user action.

### P0-2. Preserve Webview State Across Sidebar Switches
- **Problem:** `extension.ts` registers `zycode.chat` without `{ webviewOptions: { retainContextWhenHidden: true } }`, and `src/webview/main.ts` does not use `vscode.getState()` / `vscode.setState()`. Switching away to Explorer, Source Control, or Search destroys the webview DOM, losing unsent drafts, attached `@` chips, scroll offset (`followTail`), and manually toggled thinking/tool `<details>` states, while forcing full Markdown re-parses on return.
- **Solution:**
  1. Enable `{ webviewOptions: { retainContextWhenHidden: true } }` in `src/extension.ts`.
  2. Persist `{ draft, attachments, followTail, scrollTop, openDetails }` via `vscode.setState()` / `vscode.getState()` in `src/webview/main.ts` (and validate restored payload strictly before applying).
  3. Distinguish the initial `reset` emitted when a newly-created webview sends `ready` (which restores draft, chips, `<details>` open state, and scroll offset) from explicit session resets (`newChat` / `openPastSession` / `restart(false)`), which clear transient UI state cleanly.

### P0-3. Eliminate $O(N^2)$ Tool Output IPC Duplication & Unthrottled DOM Thrashing
- **Problem:**
  1. `ToolCallStore.apply()` (`src/core/tool-calls.ts`) keeps both `view.text` (from `content` text blocks) and cumulative `view.terminal.output` (from `_meta.terminal_output.data`) for `bash` calls, and `ChatController.putToolItem()` (`src/core/chat-controller.ts`) re-emits the entire growing `ToolCallView` on every streaming delta and scans `this.items` linearly via `findIndex`.
  2. `upsertTool()` (`src/webview/main.ts`) is unthrottled and executes `node.replaceChildren(...)` on every update, destroying `<details>` and `<pre>`, wiping text selections and scroll positions, and forcefully overwriting `open` based on `body.length < 400`. Likewise `entry.thought.textContent = thought` updates synchronously on every delta without throttling.
- **Solution:**
  1. In `ToolCallStore`, avoid duplicating `view.text` when `view.terminal` is active, and expose `applyWithDelta()` so pure `in_progress` terminal output appends emit a lightweight `{ type: "toolCallOutput", id, delta, status }` `HostMessage` instead of the full `ToolCallView` payload. Maintain `toolItemIndices` in `ChatController` for $O(1)$ transcript item lookup.
  2. In `src/webview/main.ts`, replace `toolEls` with persistent `toolEntries` that mutate badge, status attributes, and output `<pre>` in place without recreating `<details>`, track `userToggled` + persisted `openDetails` so user expand/collapse choices are respected, and throttle streaming `toolCallOutput` / `agentThought` DOM writes at `DRAW_INTERVAL_MS` (80 ms), flushing immediately on `completed`/`failed` or `turnEnd`.

### P0-4. Fix Commit `51b054c` Edge Cases (`@` / `/` / Model / Watcher)
- **Problems:**
  1. **Header Model Picker & Stale Context Usage:** `ChatController` supports `state.models` and `applyModel`, yet `renderHeader()` (`src/webview/main.ts`) displays `state.model` as static text without a `<select>`, and `applyModel()` (`src/core/chat-controller.ts`) retains the previous model's `this.usage` when switching to a model without `context_window` (because `agent.py` sends no `usage_update` in that case). *(Note: `CliConfig.model_reasoning` in `packages/pi-agent-cli/pi_agent_cli/config.py` is global rather than per-`[[models]]` entry, so per-model reasoning capabilities depend on future agent work tracked in P3.)*
  2. **Detached `@` Chips vs Manual Deletion:** Backspacing an `@path` token out of `<textarea>` leaves the stale `MentionAttachment` in `attachments`, causing `formatPromptWithMentions` (`src/core/mentions.ts`) to re-inject `Referenced context: ...` on send.
  3. **`WorkspaceMentions` Cache Thrashing & Exclude Gaps:** `src/ui/workspace-mentions.ts` watches `**/*` without filtering ignored folders (so `.git/` lock/pack updates, `target/`, `node_modules/`, `dist/` invalidate the cache constantly), refreshes unconditionally every 15s (`CACHE_TTL_MS`), skips user `files.exclude` / `search.exclude` entries containing braces (e.g. `**/*.{pyc,pyo}`), and does not read `.gitignore`.
  4. **Client `/` Commands in Non-`ready` States & Hardcoded `/mode` Hint:** `submit()` (`src/webview/main.ts`) gates all commands behind `state.status === "ready"`, preventing `/log`, `/config`, `/guide`, and `/restart` during `busy` or error/setup states even though the composer allows typing in `busy`. In `src/core/composer.ts`, `/mode` hardcodes `argHint: "ask | auto | always-approve"` even when `always-approve` is unavailable.
- **Solution:**
  1. Render a `<select aria-label="Model">` in `.controls` (after Mode) when `state.models?.options.length > 1`, disabled outside `ready`; clear `this.usage` prior to `setConfigOption("model", ...)` and restore on failure.
  2. Add boundary-aware `containsMentionToken()` in `src/core/mentions.ts` and sync `attachments` on `input` events in `src/webview/main.ts`.
  3. Add pure `src/core/mention-excludes.ts` supporting brace expansion (`**/*.{pyc,pyo}`), root `.gitignore` parsing (including `!` negations), expanded default build/cache excludes, and compiled relative-path filtering; use it in `WorkspaceMentions` to ignore noisy watcher events under excluded paths, watch `.gitignore` / config changes explicitly, and extend `CACHE_TTL_MS` to 300s.
  4. Compute `/mode` `argHint` dynamically from `state.mode.options`, add `canRunClientCommandInStatus()` in `src/core/composer.ts`, and allow safe local client commands (`/log`, `/config`, `/guide`, `/restart`, etc.) to dispatch outside `ready`.

---

## 3. P1 — High-Value Capabilities & VS Code Native Integration (Pure Extension)

1. **Image Input (`PromptCapabilities.image = true`):** `packages/pi-agent-cli/pi_agent_cli/agent.py` already supports `ImageContentBlock`. Support clipboard image paste (`Ctrl+V`), drag-and-drop, and attachment chips (with size/format caps) in the composer, forwarded via `AcpConnection.prompt()`.
2. **MCP Server Forwarding (`mcpServers`):** `packaging/agent/zycode-agent.spec` already bundles `pi-mcp-py`, while `AcpConnection.newSession()` sends `mcpServers: []`. Add `application`/`machine`-scoped MCP configuration (preserving D6 so workspaces cannot inject arbitrary executables) and forward stdio/HTTP/SSE definitions in `session/new` / `session/load`.
3. **Editor & Explorer Context Integration:**
   - Add `keybindings`, `editor/context` ("Add selection to zycode"), `explorer/context` ("Add to zycode context"), and file drag-and-drop into the chat input.
   - Make workspace file paths clickable across tool cards (`read`/`edit`/`write`), user `@file:L10-L20` tokens, and assistant inline code/links to open the file at line/column in VS Code.
   - Add "Insert at cursor" and "Run in terminal" (for shell blocks) actions alongside code block Copy.
   - Add a **Save all** button to the unsaved-changes notice (`NoticeAction`), and ensure `@file:L10-L20` selection mentions remain accurate when an editor buffer has unsaved edits.
4. **Multi-Root Workspaces & Folder Changes:** Listen to `vscode.workspace.onDidChangeWorkspaceFolders` in `extension.ts` (recovering from `no-folder`), align `VsCodeHost.sessionFolder()` with design section 4 (prefer active editor's workspace folder before falling back to the first `file` folder), and provide a folder switcher for multi-root workspaces.
5. **Composer Ergonomics:** Add Up/Down prompt history recall, `Esc` to cancel generation when popovers are closed, and follow-up/queued prompt handling during `busy` turns.

---

## 4. P2 — Layout, Readability & UX Polish (Pure Extension)

1. **Header & Composer Space Optimization:** Move `New chat`, `Restart`, and `Open agent.toml` actions to `zycode.chat` native `view/title` toolbar buttons (`package.json`), and compact `Model` / `Mode` / `Thinking` selectors into the composer footer so narrow sidebars maximize transcript height.
2. **Lightweight Code Syntax Highlighting:** Tokenize fenced code blocks into `<span class="tok-*">` DOM nodes (preserving zero `innerHTML` and `.code-line` copy behavior) mapped to VS Code theme tokens.
3. **Context Row Discoverability:** When using bundled agent `>= 1.0.0` and no `usage_update` arrives after the first completed turn, show a one-time dismissible info hint explaining `context_window` in `agent.toml` with an `Open agent.toml` button.
4. **Host-Backed Clipboard & Hygiene:** Route copy operations through a validated `{ type: "copy", text }` message calling `vscode.env.clipboard.writeText` (avoiding webview focus/permission quirks), and remove the production `window.__copied` test hook from `src/webview/main.ts` (replacing with a harness/test-only hook).
5. **Past Chats View Polish:** Skip `SessionsView` 60s timer refreshes when `!this.view.visible` or there are no rows; consider date grouping (`Today` / `Yesterday` / `Earlier`).

---

## 5. P3 — Cross-Platform Robustness, Agent Coordination & Distribution

1. **Windows Path & Process-Tree Cleanup:**
   - Fix `DiffDocuments.open()` (`src/ui/diff-documents.ts`) using `path.posix.basename` on Windows backslash paths (handle both `/` and `\`).
   - Fix `AgentProcess.stop()` (`src/core/agent-process.ts`) force-kill on Windows using `taskkill /T /F /PID` so PyInstaller `--onefile` parent/child and `bash` descendants are reaped cleanly without leaving orphan processes or `_MEI*` temp folders.
2. **Agent-Coordinated Improvements (`packages/pi-agent-cli/`):**
   - **Live `thinking_level` via ACP `session/set_config_option`:** Expose `thinking_level` (and per-model reasoning capability metadata) over standard ACP so switching levels no longer requires editing global `agent.toml`, restarting the agent (~2s), losing `DiffStore`, and resetting `always-approve`.
   - **Structured Diff Blocks in `events.py`:** Pass `args` / `oldText` + `newText` in ACP `diff` blocks for `write` and `edit`, eliminating `DiffTracker` pre-image races in `auto`/`always-approve` mode and fuzzy-edit limitations.
3. **Verification, Packaging & Documentation Sync:**
   - Add `@vscode/test-electron` extension host integration tests in CI (covering real Webview messaging, terminal `--setup`, diff editor, and clipboard).
   - Evaluate a universal fallback VSIX (for `win32-arm64` / `alpine`), startup latency (`--onedir` vs `--onefile`), and non-OpenAI provider bundling.
   - Sync `vscode/README.md` (which still states "No model choice, no `@file` mentions yet" at line 98) and add visual screenshots/GIFs.

---

## 6. Execution & Verification Record

- **P0 Implementation (2026-11-19):** Completed P0-1 through P0-4 in `vscode/`:
  - **P0-1:** Added `pendingPermissions` to `StateMessage`, pure fallback builder/resolver (`src/core/permission-fallback.ts`), hidden-view modal fallback (`src/ui/permission-fallback.ts`, wired in `src/ui/chat-view.ts`), and approval indicator/tooltip (`src/core/status-row.ts`, `src/ui/status-bar.ts`).
  - **P0-2:** Enabled `retainContextWhenHidden: true` in `src/extension.ts`, persisted/restored `{ draft, attachments, followTail, scrollTop, openDetails }` via `vscode.getState`/`setState` in `src/webview/main.ts` (initial `reset` restores, subsequent `reset` clears), and added state-mock support to `scripts/webview-harness.mjs`.
  - **P0-3:** Removed duplicate `view.text` storage when `view.terminal` is present and added `ToolCallStore.applyWithDelta` (`src/core/tool-calls.ts`), `toolCallOutput` incremental message (`src/core/protocol.ts`), $O(1)$ `toolItemIndices` lookup and deduplicated `canDiff` finish emissions (`src/core/chat-controller.ts`), and in-place throttled DOM updates for tool outputs and thinking blocks (`src/webview/main.ts`).
  - **P0-4:** Added Model `<select>` in header and cleared stale `usage` on model switch with rollback on failure (`src/webview/main.ts`, `src/core/chat-controller.ts`), added boundary-checked `containsMentionToken` chip sync (`src/core/mentions.ts`, `src/webview/main.ts`), added `src/core/mention-excludes.ts` + `.gitignore` / brace-expansion / excluded-event filtering in `src/ui/workspace-mentions.ts` (300 s cache TTL), made `/mode` `argHint` dynamic, and enabled safe non-`ready` client `/` commands (`src/core/composer.ts`, `src/webview/main.ts`).
- **Verification (`vscode/`):**
  - `npm run check`: `tsc --noEmit -p tsconfig.json`, `tsc --noEmit -p src/webview/tsconfig.json`, `eslint .`, and `vitest run` passed (`25` test files / `413` tests passed; `3` opt-in real-agent files / `18` tests skipped, including `1` `locate` platform skip and `1` `thinking` POSIX-symlink skip on Windows).
  - `npm run build` and `node esbuild.mjs --production`: both host (`dist/extension.js`) and webview (`dist/webview.js`) bundled cleanly.
  - **Not verified in a live VS Code host:** `PermissionFallback` (`vscode.window.showWarningMessage`), `StatusBar` rendering, `WorkspaceMentions` live `FileSystemWatcher`, and `retainContextWhenHidden` across real sidebar tab switches (covered by typecheck/lint + pure unit tests).
