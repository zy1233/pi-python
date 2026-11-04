# zycode VS Code extension: design

> Scope: a VS Code extension (`zycode.zycode`) that runs the zycode agent and shows a chat for it. This record gives the design of Phase 1: the pre-release.
> Decided: 2026-11-04. The user adopted the plan of the research report (ZY-59, sections 1 to 6 and 9) and chose: route (a), an ACP-client extension with its own webview chat; publisher `zycode`; extension name `zycode`; display name "zycode"; five targets; no telemetry; no code signing; Trusted Publishing for the publish pipeline.
> Status: design only. The code comes in the next pull requests: the core (`packages/pi-vscode/`), the chat, the packaging.
> Code (planned): `packages/pi-vscode/`. Agent side (unchanged): `packages/pi-agent-cli/pi_agent_cli/{agent,events,auth_methods,permissions,trust_prompt,permission_modes}.py`.
> Facts about the agent in this record come from the source tree at `9c763c8` and from a run of the real agent over ACP (section 1). Facts about VS Code come from the research report; they were true on 2026-11-04.

---

## 0. Summary

- **What it is.** An ACP client in an extension. It starts `zycode-agent` as a child process, talks ACP over stdio, and shows the session in a chat view.
- **Where the agent comes from.** Each platform VSIX holds the `zycode-agent` binary of one GitHub Release (`zycodeAgentVersion`, checked with sha256). The setting `zycode.agentPath` replaces it.
- **Three parts, one rule.** The *host* (extension host, Node) owns the agent process and the ACP connection. The *webview* only draws and sends messages. The *core* (ACP client, state, diff) has no `vscode` import, so it runs in unit tests without VS Code.
- **Who decides what.** The agent decides what needs a question. The extension shows the question and sends the answer. It never answers for the user.
- **Two kinds of trust.** VS Code Workspace Trust decides if the extension starts at all. The agent's own project trust decides if the project may load its extensions. The text of the two prompts says so.
- **Not in Phase 1.** Session list and resume, model choice, images, `@file`, MCP servers, fallback package, and everything of report section 3.3 from Phase 2 on (section 12).

## 1. Facts the design rests on

| # | Fact | How known |
|---|------|-----------|
| F1 | The agent speaks ACP v1 over stdio. `zycode-agent` with no arguments starts it. `--setup` runs the setup dialog and needs a terminal. | `agent.py`, `__main__.py`, `setup_command.py`; the spike |
| F2 | `initialize` returns `agentInfo = {name: "pi-agent-cli", version}`. The version is the version of the Python package, and so of the Release. | Run against the real agent: `0.8.0` |
| F3 | A client that sets `clientCapabilities.auth.terminal = true` gets `authMethods: [{id: "pi-setup", type: "terminal", args: ["--setup"], env: {}}]`. Any other client gets `[]`. | `auth_methods.py`; the spike |
| F4 | With no model and no key, `session/new` fails with code `-32000` and `data.authMethods = ["pi-setup"]` to a client that can run the login. No session is made. | `agent.py`; the spike |
| F5 | The agent uses no `fs/*` and no `terminal/*` method of the client. It reads and writes the disk itself, and it runs `bash` itself. | Research report section 5; `events.py` |
| F6 | A `tool_call` update comes first (`status: pending`, `rawInput` set). In `ask` mode a `session/request_permission` follows, **before** the tool runs. Its `toolCall.title` is the bare tool name (`write`), and `rawInput` is the same as in the update. The options are `allow-once` (`allow_once`) and `reject-once` (`reject_once`). | Run against the real agent (`tests/_tool_agent.py`, scripted `write` and `edit`) |
| F7 | **The agent does not send a `diff` content block in practice.** After `edit`, the update has a text block (`Successfully replaced 1 block(s) in e.txt.`) and `rawOutput.details.diff`, a unified patch with `--- path` and `+++ path` headers (new text, written already). After `write`, the update has a text block only (`Successfully wrote 12 bytes to a.txt`) and no diff. `path` is relative to the session folder. | Run against the real agent. The code in `events._result_content` can make a `diff` block, but the `tool_execution_end` event that the harness sends has no `args`, so it does not |
| F8 | A `bash` call has a `terminal` content block and `_meta.terminal_info`. Output comes as `_meta.terminal_output.data` (deltas) and as a text block that holds the whole output so far. The end has `_meta.terminal_exit.exit_code`. The text of a failed call ends with `Command exited with code N`. | Run against the real agent |
| F9 | The project-trust question is a `session/request_permission` with `toolCall.toolCallId` that starts with `trust-`, `rawInput = {project, resources, changed}`, and two options: `dont-trust-project` (`reject_once`, first) and `trust-project` (`allow_always`). There is no `allow_once` on purpose. An agent message with the explanation comes just before it. | `trust_prompt.py` |
| F10 | `session/new` returns `modes` and the config options `model` and `mode` (values `ask` and `auto`). `always-approve` shows as the current value only if `agent.toml` sets it. **The v0.8.0 binary has no `mode`.** Auto is "Unreleased" (`CHANGELOG.md`); the probe of the 0.8.0 binary shows `configOptions: ["model"]` and no `modes`. | `permission_modes.py`; the spike output; `git log v0.8.0..HEAD` |
| F11 | When stdin closes, the agent stops its tools and exits with code 0. SIGTERM and SIGHUP do the same. | `packages/pi-agent-cli/AGENTS.md` "Stopping the agent" |
| F12 | The agent keeps the key it had at start. After `--setup`, a running agent re-reads `agent.toml` and `local.env` at `session/new` only while it still has a configuration problem. A **changed** key needs a new process. | `packages/pi-agent-cli/AGENTS.md` "First run" |
| F13 | The binary starts in about 2 s (cold, one file). The Linux build needs glibc 2.35 or newer. It holds only the OpenAI-format provider. | Research report 4.3 |

F6 to F8 decide the diff design (section 8). F10 decides how the permission mode is shown (section 9).

## 2. Architecture

```
VS Code window (extension host, Node, runs where the folder is)
├── extension.ts              activation, commands, status bar, settings
├── ui/        (uses vscode)  ChatViewProvider, permission fallback, diff documents, setup terminal
├── core/      (no vscode)    AgentProcess, AcpConnection, ChatController, tool-call model, diff maths
└── webview (browser sandbox) one script: draws the chat, sends messages
        ▲ │  postMessage (typed, section 6)
        │ ▼
   ChatViewProvider ◄──► ChatController ◄──► AcpConnection ◄──stdio──► zycode-agent
```

Rules:

1. `core/` has no `import "vscode"`. It gets what it needs from `vscode` through small interfaces (`Host`: settings, workspace folder, trust, logging). The unit tests give it a fake host and a fake agent.
2. The webview has no access to the file system, the agent or the network. It is a view: it gets state and events, and it sends intents. Its content security policy allows only the extension's own script and style, with a nonce, and no remote content.
3. The extension runs in the workspace host (`extensionKind: ["workspace"]`). With Remote-SSH, WSL or a dev container, the agent then runs next to the files it edits, and VS Code installs the platform VSIX of the remote machine.
4. One agent process for each window. One active chat session at a time. A new chat is a new `session/new` on the same process.
5. Text that the agent or the model sends is data. The webview builds the page with DOM nodes and `textContent`. It never sets `innerHTML` from agent text. Markdown is a small renderer that makes only paragraphs, code spans, code blocks, lists and links; a link opens through the host, and only `http`, `https` and `mailto`.

### 2.1 Files

```
packages/pi-vscode/
├── package.json            manifest, zycodeAgentVersion, scripts
├── src/
│   ├── extension.ts
│   ├── core/               agent-process, locate, acp-connection, chat-controller, tool-calls, diff, version, protocol
│   ├── ui/                 chat-view, permission-fallback, diff-documents, setup-terminal, status-bar
│   └── webview/            main.ts, render.ts, markdown.ts, style.css
├── test/                   vitest unit tests, fake agent, opt-in test against the real agent
├── scripts/                package (download, sha256, vsce), notices, check-vsix
├── agent-assets.json       the pinned Release assets (name, size, sha256 per target)
├── media/icon.png          256x256
├── LICENSE, README.md
└── THIRD-PARTY-NOTICES     generated at package time (not committed)
```

## 3. The agent process

### 3.1 Which program

`resolveAgentCommand(host)` returns `{command, args, source}` in this order:

1. `zycode.agentPath` (a non-empty string), with `zycode.agentArgs` (default `[]`). `source = "setting"`. Example for a Python install: path `python`, args `["-m", "pi_agent_cli"]`. The value is an absolute path, or a bare program name that the operating system finds on `PATH` (the user named it). A relative path with a folder part (`./agent`, `bin/agent`) is an error: it would depend on the working folder.
2. The bundled binary: `<extension>/bin/zycode-agent` (`.exe` on Windows). `source = "bundled"`.
3. Neither exists (a development checkout, or an unknown platform): state `unavailable`, with the text that says what to do (set `zycode.agentPath`). The extension does not search `PATH`: a program that the user did not name is not started.

Before the start of a bundled binary on Linux and macOS, the extension runs `chmod 0o755`. A VSIX installed by VS Code can lose the exec bit (report section 4.3: unknown). A `chmod` error is not fatal; the start then shows the real error.

### 3.2 Start

- `child_process.spawn(command, args, {stdio: ["pipe", "pipe", "pipe"], cwd: <workspace folder>, env: process.env, windowsHide: true})`. No shell.
- The environment is the one of the extension host. The agent needs `PATH` and the provider key variables. The extension sets nothing and removes nothing. `PI_HOME` stays what the user has, so the extension and `zypi` share `~/.pi-python`.
- stdout is the ACP channel and nothing else reads it. stderr goes to the output channel `zycode` (line by line, at most 1 MiB kept for the "agent exited" message). The extension does not log prompts, answers or tool input. It logs method names, ids, state changes and agent stderr.
- Spawn errors (`ENOENT`, `EACCES`, `ENOEXEC`) become the state `error` with the code and the path in the text.

### 3.3 States

```
stopped ──start──► starting ──initialize ok──► ready ──prompt──► busy ──turn end──► ready
                       │                         │                  │
                       │ error / exit            │ session/new      │ session/cancel
                       ▼                         ▼ auth_required    ▼
                     error ◄──── exit ──── any   needs-setup      (cancelling) ─► ready
```

- `needs-setup`: `session/new` failed with the login error (section 7).
- `error`: the process did not start, exited, or `initialize` failed. A prompt in flight ends with the error text. The chat shows the last lines of stderr and a **Restart agent** button. The extension does not restart the agent by itself in a loop. It restarts on the button, on the command, and when `zycode.agentPath`, `zycode.agentArgs` or the permission mode change.
- `starting` has a timeout of 60 s for `initialize` (the cold start is about 2 s).

### 3.4 Stop

On `deactivate`, on **Restart agent**, and when the window closes: cancel the open prompt (`session/cancel`), answer open permission requests with `cancelled`, close stdin, and wait for the exit. If it does not exit in 3 s, send SIGTERM (`child.kill()` on Windows). After 2 s more, SIGKILL. A tool process that the agent could not reap is left to the operating system; stdin close is the normal way and the agent reaps the tools on it (F11).

### 3.5 Version check

After `initialize`, compare `agentInfo.version` with `zycodeAgentVersion` of `package.json`.

| Source | Result |
|--------|--------|
| bundled, equal | nothing |
| bundled, different | a warning in the log and one notification: "The bundled agent reports version X, the extension expects Y." The session goes on. A bundled binary that does not match is a packaging fault, not the user's fault. |
| setting | The log gets one line with the version. No warning: the user chose this program. |
| any, `protocolVersion` is not 1, or `agentInfo.name` is not `pi-agent-cli` | state `error`: "This program does not speak the zycode agent protocol." |

`zycodeAgentVersion` is a field of `package.json` that the extension reads at run time (`context.extension.packageJSON`). The packaging script fails if it differs from `agent-assets.json` (section 11).

## 4. The ACP session flow

The core uses `@agentclientprotocol/sdk` (stable v1 entry point; the `unstable` entry points are not used). It makes the connection with `client({name}).onRequest(...).onNotification(...).connect(stream)` and keeps the `ClientConnection` open for the life of the process.

```
host                                              agent
 │── initialize {protocolVersion: 1,               │
 │     clientCapabilities: {auth: {terminal: true}},│
 │     clientInfo: {name: "zycode-vscode", version}}►
 │◄─ agentInfo, authMethods, agentCapabilities ─────│
 │── session/new {cwd, mcpServers: []} ────────────►│
 │◄─ sessionId, configOptions, modes ───────────────│   (or -32000 auth_required: section 7)
 │── session/set_config_option {mode} ─────────────►│   only if the setting differs from the current value
 │◄─ session/update (available_commands_update) ────│
 │── session/prompt {sessionId, prompt: [text]} ───►│
 │◄─ session/update * (message, thought, tool_call) │
 │◄─ session/request_permission ────────────────────│   the client answers; the tool runs after
 │── {outcome: selected, optionId} ────────────────►│
 │◄─ session/prompt result {stopReason} ────────────│
 │── session/cancel {sessionId} (notification) ────►│   when the user presses Stop
```

Details:

- **Client capabilities.** `auth: {terminal: true}` and nothing else. The extension does not declare `fs` or `terminal` (F5). A client that declares them would be asked nothing, but a client that does not declare them is the honest one.
- **`cwd`.** The folder of the active editor if it belongs to a workspace folder, else the first workspace folder. With no folder, the state is `no-folder` and the chat says to open one. A virtual workspace is refused by the manifest (`virtualWorkspaces: false`).
- **`mcpServers`.** `[]` in Phase 1.
- **Updates.** The notification handler puts each `session/update` on a queue, so that the controller applies them in the order they arrive. An update for another session id is dropped. Updates before the `session/new` response is known are held and applied after it.
- **Update kinds handled:** `agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `tool_call_update`, `user_message_chunk` (only for replay; Phase 1 has none), `available_commands_update` (stored; Phase 2 uses it), `current_mode_update` and `config_option_update` (update the status). Any other kind is ignored with one log line (the protocol is open).
- **Prompt.** One text block. The turn ends with `stopReason` (`end_turn`, `cancelled`, `max_tokens`, `refusal`, ...). A non-`end_turn` reason other than `cancelled` shows as a notice line.
- **Cancel.** Stop sends `session/cancel`, answers the open permission requests with `{outcome: "cancelled"}`, and waits for the prompt result (the agent ends the turn as `cancelled`). While it waits, the state is `cancelling` and Stop is disabled. After 10 s with no answer the extension shows "The agent did not stop. Restart it?".
- **Unsaved files.** The agent reads the disk. Before it sends a prompt, the controller asks the host for the dirty documents under the session folder. If there are any, it shows one notice line: "N file(s) have unsaved changes. The agent reads the files on disk." The extension does not save for the user.
- **A second prompt** while one is in flight is refused in the view (the Send button is Stop). The agent also refuses a second prompt from the client (`busy`).
- **Errors** from a request (`RequestError` with `code` and `data`) go to the chat as a notice with the message. Only `-32000` with `authMethods` has its own path (section 7).

## 5. Permission and project-trust prompts

Both arrive as `session/request_permission`. The handler returns a promise that the user's click settles. It never settles by itself, except on cancel, on close and on process exit (then `cancelled`).

### 5.1 Tool permission

The prompt is a card in the chat, below the tool call it is about (matched by `toolCall.toolCallId`; the `tool_call` update came before it). The card shows:

- the title of the tool call from the `tool_call` update (`write a.txt`, or the command of `bash`), because the title of the request is only the tool name (F6);
- a short summary of `rawInput`: the path for `read`, `edit`, `write`; the command for `bash` (the whole command, in a code block, scrolled; never cut, because the user decides on it);
- one button for each option, in the order the agent gave, named with the agent's option name. The kind sets the style (`allow_*` primary, `reject_*` plain). No option has focus at first: Enter does not allow.
- for `edit` and `write`, a **Show diff** button (section 8).

The user's choice goes back as `{outcome: "selected", optionId}`. Closing the card is not possible; Stop answers `cancelled`.

If the chat view is not visible when a request arrives (the user closed or moved the view), the host also shows a modal `window.showWarningMessage` with the same text and options, and the first answer wins. A prompt that nobody can see would leave the agent waiting for ever. The modal has no "always" option the agent did not send.

### 5.2 Project trust

Detected by F9: the id starts with `trust-` **and** `rawInput` has `project` and `resources`. Anything else is a tool permission. The card is styled as a question about the project, not about a tool call, and it holds this fixed text before the agent's own explanation message:

> This is the zycode agent's own question about this project. It is not the VS Code Workspace Trust question, which you already answered. It decides if zycode may load the extensions, saved workflows, prompt files and skills that this project ships.

Rules:

- Buttons are the two options the agent sent, in that order: `Don't trust` first, then `Trust and remember`. The extension adds none. In particular there is no "allow once", and no setting turns the answer on in advance.
- The explanation arrives as an `agent_message_chunk` just before the request (the agent's own design). The extension shows it as it shows any agent message. It does not parse it.
- The extension never answers this request by itself, in any permission mode. `always-approve` in `agent.toml` makes no difference here: it is the agent that asks.
- A request that has `rawInput.resources` but a wrong option set (for example an `allow_once` option) is shown as a tool permission with the text "unexpected trust question", so a wrong agent cannot get a trust answer by a button the design did not plan.

### 5.3 Permission mode

See section 9. The mode only changes which calls the agent asks about. The extension has no rule of its own.

## 6. Messages between the webview and the host

All messages are JSON objects with a string `type`. The types live in one file (`core/protocol.ts`) that both sides import. The host validates every message from the webview (the webview is not trusted: a page script can send anything) and ignores unknown types.

### 6.1 Host to webview

| `type` | Fields | Meaning |
|--------|--------|---------|
| `state` | `status` (`stopped`, `starting`, `ready`, `busy`, `cancelling`, `needs-setup`, `error`, `no-folder`, `untrusted`, `unavailable`), `detail?` (text), `agent?` (`{version, source}`), `cwd?`, `mode?` (`{current, options[]}`), `model?` (text), `canRestart` | The full status. Sent at each change and when the view loads. |
| `reset` | `transcript[]` | The view loaded or a new chat started. `transcript` is what the controller holds, so that a hidden view that loads again shows the same chat. |
| `userMessage` | `id`, `text` | The prompt that was sent. |
| `agentText` | `messageId`, `text` | Append to the current assistant text. |
| `agentThought` | `messageId`, `text` | Append to the current thinking block. |
| `toolCall` | `toolCall` (see below) | New or updated tool call. The view replaces the entry with the same `id`. |
| `permission` | `requestId`, `kind` (`tool` or `trust`), `toolCallId?`, `title`, `summary`, `options[]` (`{id, name, kind}`), `canDiff` | Show a card. |
| `permissionDone` | `requestId`, `outcome` (`selected`, `cancelled`), `optionId?` | The card closes, and shows what was chosen. |
| `notice` | `level` (`info`, `warning`, `error`), `text` | A line that is not part of the model's answer. |
| `turnEnd` | `stopReason` | The turn is over. |
| `diffState` | `toolCallId`, `available` | Updates the **Show diff** button. |

`toolCall` object: `{id, title, kind, status, rawInput?, text?, terminal?: {output, exitCode?}, paths?: string[]}`. `text` is the last text block of the update (the whole output so far for `bash`). It is plain text.

### 6.2 Webview to host

| `type` | Fields | Meaning |
|--------|--------|---------|
| `ready` | | The script has loaded. The host sends `state` and `reset`. |
| `send` | `text` | A prompt. The host drops it if the state is not `ready`, or the text is empty. |
| `cancel` | | Stop the turn. |
| `newChat` | | Close the session (if `session/close` is offered) and open a new one. |
| `permissionAnswer` | `requestId`, `optionId` | The user chose. The host accepts only an `optionId` that the request listed. |
| `showDiff` | `toolCallId` | Open the diff. |
| `setMode` | `value` | `ask` or `auto`. The host sets `zycode.permissionMode` and the session option. |
| `runSetup` | | Run the first-run setup in a terminal (section 7). |
| `restartAgent` | | Section 3.4 and a start. |
| `openLog` | | Show the output channel. |
| `openLink` | `url` | The host opens it if it is `http`, `https` or `mailto`. |

There is no message that reads a file, runs a command, or sets a setting other than `zycode.permissionMode`.

## 7. First run: `--setup` through `auth.terminal`

The user starts the extension with no model and no key. The flow:

1. `initialize` offers `authMethods: [{id: "pi-setup", type: "terminal", args: ["--setup"], env: {}}]` (F3).
2. `session/new` fails with `-32000` and `data.authMethods` that names `pi-setup` (F4). The state is `needs-setup`. The chat shows: "zycode needs a model and an API key. Run the setup in a terminal." and a **Run setup** button. The extension does not open the terminal without a click.
3. **Run setup** (also the command `zycode: Run setup`): `window.createTerminal({name: "zycode setup", shellPath: <agent command>, shellArgs: [...agentArgs, ...method.args], env: {...method.env}, cwd: <session folder>})`, `show()`. The setup dialog runs in this terminal, with the same program that serves the session.
4. The extension waits for `window.onDidCloseTerminal` of that terminal (no timeout: the user is typing). Then:
   - exit code 0: **restart the agent** (F12: a replaced key needs a new process), then `session/new` again.
   - another code, or the terminal closed with no code: the chat shows "Setup did not finish." and the button again. Setup saves nothing when its input ends (`packages/pi-agent-cli/AGENTS.md`, "First run"), so nothing half-written is left.
5. If `session/new` fails again with the login error, go back to step 2. There is no loop: each pass needs a click.

Other facts:

- The key is typed in the terminal. It never goes through the webview, the host's own variables or the log. The extension does not use `SecretStorage` in Phase 1: the agent and `zypi` read `~/.pi-python`, and a second place for the key would make the two disagree (report section 5).
- The command is also available when a session exists, to change the model or key later. It then ends with a restart of the agent and a new chat.
- `shellPath` with a binary and `shellArgs` makes VS Code run the binary directly in the pty. On Windows the agent binary is a console program, and this works the same. (Not verified on Windows or macOS: section 13.)

## 8. The diff view

What the agent sends decides this design (F6 to F7). The wire has no usable old and new text. So the extension builds the diff itself, from three sources, and it uses `vscode.diff` with two virtual documents.

**Documents.** The extension registers a `TextDocumentContentProvider` for the scheme `zycode-diff`. A URI is `zycode-diff:/<toolCallId>/<before|after>/<file name>`, so that the editor picks the language from the file name. The provider returns the text it holds for that id and side. It holds text in memory only, at most 50 tool calls and 4 MiB for the whole store, oldest out first.

**Pre-image.** When a `tool_call` update of kind `edit` has `rawInput.path`, the controller starts to read that file (relative paths resolve against the session folder; the host reads the editor buffer if the file is open and **not** dirty, else the disk; a missing file is a *new file*, recorded as `null`). The permission handler awaits this read before it shows its card, so in `ask` mode the pre-image is taken before the tool can run. In a mode where no question is asked, the read races with the tool. Section 8.1 says what happens then.

**Post-image and result by tool:**

| Tool | Before | After |
|------|--------|-------|
| `write` | the pre-image, or empty for a new file | `rawInput.content` |
| `edit` | the on-disk file with `rawOutput.details.diff` applied backwards; if that fails, the pre-image | the on-disk file after the call |

Before the tool runs (at the permission card), **Show diff** gives a *proposed* diff: for `write`, pre-image against `rawInput.content`; for `edit`, pre-image against the pre-image with `rawInput.edits` applied (each `oldText` must occur exactly once; if one does not, the button is disabled and the card says "diff not available"). After the tool has run, the same button shows the result diff.

If the agent one day sends a `diff` content block with both `oldText` and `newText`, the extension uses it and ignores its own maths. A block with `newText` only and no `oldText` is treated as a unified patch if it starts with `---` or `@@`, and is shown as plain text otherwise.

The diff is for reading. It has no accept or reject buttons, and the extension does not write any file (F5). Per-hunk review is not in Phase 1.

### 8.1 Limits of this design

- Reverse-applying a patch fails when the file changed after the call. The extension then shows the pre-image against the file on disk, or only a message.
- For `write` with no question asked (`auto` or `always-approve`), the pre-image can be read after the file is written. If the extension cannot show that the pre-image was taken before the call (the read finished after `tool_call_update` with `completed`), it shows the new content only and says "old content not available". It does not show a wrong diff.
- A change to the agent would remove this work: send `oldText` and `newText` in the `diff` block for `write` and `edit`. That is a change of `events.py`, and it is outside this extension. It is noted here as the better long-term design.

## 9. Settings

| Setting | Type, default | Scope | Meaning |
|---------|---------------|-------|---------|
| `zycode.agentPath` | string, `""` | `machine` | Program to run instead of the bundled binary. |
| `zycode.agentArgs` | string[], `[]` | `machine` | Arguments for it. |
| `zycode.permissionMode` | `"ask"` or `"auto"`, `"ask"` | `application` | The mode of every session of the extension. |
| `zycode.showThinking` | boolean, `true` | `window` | Show the thinking blocks (collapsed). |

**Scopes are a security choice.** A setting that names a program, or loosens permissions, must not come from the folder that the agent works in. `machine` and `application` settings are read from the user's settings only, never from `.vscode/settings.json` or a `.code-workspace` file. Workspace Trust already stops the extension in an untrusted folder, but a trusted folder is trusted to run its tasks, and not to choose the agent.

**Mode.** `ask` and `auto` are the two values the agent lets a client choose (F10). After `session/new`, if the response has a config option with id `mode` (category `mode`) and its current value is not the setting, the controller sends `session/set_config_option`. The setting wins over `permission` in `agent.toml`, **including `always-approve`**: the extension never runs a session in `always-approve`, and it says so in the README. A change of the setting applies to the open session at once. The status shows the mode.

When the response has no `mode` option (the v0.8.0 binary, F10), the extension cannot set it. The agent is then in `ask` (the default of `agent.toml`) or in whatever `agent.toml` says. The view hides the mode switch, shows "mode: set by agent" and, if the setting is `auto`, one notice: "This agent version has no Auto mode."

**The README says** (what the agent's own notes say, and no more): Auto guards what a call writes or runs; it does not guard what a call reads or sends, and it is not a sandbox.

**Status.** One status bar item (`zycode: ready | busy | setup | error`, mode in the tooltip) on the left. A click focuses the chat. The chat header shows the same status, the agent version and source, the model name from the `model` option (read only in Phase 1), and the mode.

**Workspace Trust and virtual workspaces.** `capabilities.untrustedWorkspaces = {supported: false, description: "zycode runs an agent that reads and writes files and runs commands in your folder."}` and `capabilities.virtualWorkspaces = {supported: false, description: "..."}`. VS Code then does not activate the extension in Restricted Mode. As a second guard the core checks `workspace.isTrusted` before the start, and the view shows `untrusted` if it is false (for example after the user turns the extension on by hand through a path that VS Code allows). `onDidGrantWorkspaceTrust` starts the flow again. This is VS Code's question. It is a different question from the agent's project trust (section 5.2), and the README says so in one short paragraph.

**Telemetry.** None. The extension sends nothing to the authors. It has no telemetry code, no analytics package, no update ping. The README has a privacy section: the agent sends the conversation, and the files it reads, to the model service that the user configured in `agent.toml`; the extension and the authors see none of it. `isTelemetryEnabled` is not used because nothing is sent.

## 10. Commands

| Command | Does |
|---------|------|
| `zycode.openChat` | Focus the chat view. Start the agent if it is stopped. |
| `zycode.newChat` | A new session. |
| `zycode.stop` | Stop the turn. |
| `zycode.restartAgent` | Stop and start the agent. |
| `zycode.runSetup` | Section 7. |
| `zycode.showLog` | Show the output channel. |

Activation events: `onView:zycode.chat`, `onCommand:*` of the commands above. Not `*`: the extension does not start when VS Code starts. The view is in its own activity bar container with the zycode icon (a monochrome SVG for the container; the PNG is the marketplace icon).

## 11. Packaging and version pinning

**Version rules.** Extension version `0.1.0`: an odd minor, because the first releases are pre-releases (`vsce package --pre-release`; Marketplace takes `major.minor.patch` only, and a release must have a version that no pre-release has). `engines.vscode` is `^1.90.0` (it must be at least 1.63 for pre-releases; 1.90 has the webview and terminal API that the design uses). The extension version and the agent version are separate: `package.json` has `zycodeAgentVersion` (`"0.8.0"` at the start).

**The pin.** `agent-assets.json` holds, for the pinned Release tag, one entry for each target: asset name, byte size and sha256. The values are copied from the Release once, by a person, and reviewed in a pull request. The script does not trust the Release page for them: it checks the downloaded file against the file in the repository. A different sha256 on the Release is a failure, not an update.

| `vsce --target` | Release asset | In the VSIX |
|-----------------|---------------|-------------|
| `linux-x64` | `zycode-agent-linux-x86_64-<v>.tar.gz` | `bin/zycode-agent` |
| `linux-arm64` | `zycode-agent-linux-aarch64-<v>.tar.gz` | `bin/zycode-agent` |
| `darwin-arm64` | `zycode-agent-darwin-aarch64-<v>.tar.gz` | `bin/zycode-agent` |
| `darwin-x64` | `zycode-agent-darwin-x86_64-<v>.tar.gz` | `bin/zycode-agent` |
| `win32-x64` | `zycode-agent-windows-x86_64-<v>.zip` | `bin/zycode-agent.exe` |

No `alpine-*` (the binary needs glibc), no `linux-armhf` (no binary), no `win32-arm64` (not tested).

**Local script** (`scripts/package.mjs <target>|all`): check `zycodeAgentVersion == agent-assets.json version`; download the asset (HTTPS from GitHub Releases) to a cache folder; compare size and sha256 with the pin; extract the one file; set mode `0755` (POSIX targets); run the build (esbuild bundles host and webview); generate `THIRD-PARTY-NOTICES`; run `vsce package --target <t> --pre-release`; run `check-vsix`. The check opens the VSIX and verifies: the binary is there, with the exec bit (the bit survives only when packaging on Linux, so the script refuses to package on Windows); `LICENSE`, `THIRD-PARTY-NOTICES` and the PNG icon are there; there is no `node_modules`, no source map, no `.env`; the manifest has the right target and the pre-release flag.

**`THIRD-PARTY-NOTICES`.** One file with two parts. Part 1: the Python distributions in the agent binary. The script makes a clean virtual environment, installs the same packages as the agent build (`pi-agent-cli-lc` at `zycodeAgentVersion` and its extras that `packaging/agent/zycode-agent.spec` includes), and reads name, version, license and license files from each distribution's metadata (`importlib.metadata`). It adds a fixed entry for CPython (PSF-2.0) and for PyInstaller's bootloader (GPL-2.0 with the bootloader exception), and a line for the native libraries that CPython links in. Part 2: the npm packages that esbuild bundled into `dist/extension.js` (from the esbuild metafile), with their license files. A distribution with no license text is listed with a note, and the script fails the build if a license is "UNKNOWN" and is not on a reviewed allow list. The file is not committed.

**Other files.** `LICENSE` is a copy of the repository's `LICENSE` (MIT). The README first screen says "unofficial; not affiliated with pi or Microsoft". The README also says: the binary is not code-signed; it holds only the OpenAI-format provider; Linux needs glibc 2.35 or newer; the first start takes a few seconds; Auto is not a sandbox. `.vscodeignore` leaves out everything that is not `dist/`, `bin/`, `media/`, `package.json`, the README, the CHANGELOG, the LICENSE and the notices.

**Draft of the publish workflow.** It lives here as text. It becomes `.github/workflows/vscode-extension.yml` only when the user approves a change under `.github/workflows/`. The name of the file and the environment name are the ones that the Trusted Publishing policies will use.

```yaml
name: VS Code extension

on:
  pull_request:
    paths:
      - ".github/workflows/vscode-extension.yml"
      - "packages/pi-vscode/**"
  workflow_dispatch:
    inputs:
      publish:
        description: "Publish to Marketplace and Open VSX (needs approval of the environment)"
        type: boolean
        default: false

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci
        working-directory: packages/pi-vscode
      - run: npm run check        # type check, lint, unit tests
        working-directory: packages/pi-vscode

  package:
    needs: test
    runs-on: ubuntu-24.04            # Linux: the exec bit of the binary survives only here
    strategy:
      fail-fast: false
      matrix:
        target: [linux-x64, linux-arm64, darwin-arm64, darwin-x64, win32-x64]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }   # for THIRD-PARTY-NOTICES
      - run: npm ci
        working-directory: packages/pi-vscode
      - run: node scripts/package.mjs ${{ matrix.target }}   # download, sha256, build, notices, vsce package, check-vsix
        working-directory: packages/pi-vscode
      - uses: actions/upload-artifact@v4
        with:
          name: vsix-${{ matrix.target }}
          path: packages/pi-vscode/*.vsix
          if-no-files-found: error

  smoke:
    needs: package
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - uses: actions/download-artifact@v4
        with: { name: vsix-linux-x64, path: vsix }
      - run: npm ci
        working-directory: packages/pi-vscode
      - run: node scripts/smoke-vsix.mjs ../../vsix/*.vsix   # unpack, start the bundled binary, initialize over ACP, check agentInfo.version
        working-directory: packages/pi-vscode

  publish:
    if: github.event_name == 'workflow_dispatch' && inputs.publish
    needs: [package, smoke]
    runs-on: ubuntu-24.04
    environment: vscode-publish        # required reviewers
    permissions:
      contents: read
      id-token: write                  # Trusted Publishing (OIDC)
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - uses: actions/download-artifact@v4
        with: { pattern: vsix-*, path: vsix, merge-multiple: true }
      - run: npm ci
        working-directory: packages/pi-vscode
      - name: Visual Studio Marketplace
        working-directory: packages/pi-vscode
        run: |
          for f in ../../vsix/*.vsix; do
            npx @vscode/vsce publish --oidc --pre-release --packagePath "$f"
          done
      - name: Open VSX
        working-directory: packages/pi-vscode
        run: |
          for f in ../../vsix/*.vsix; do
            npx ovsx publish --trusted-publishing --pre-release "$f"   # flag as in ovsx 1.3.0: check before the first use
          done
```

Notes on the draft:

- It has **no** `workflow_run` and no tag trigger. The extension version is not the agent version, so a new agent Release must not publish by itself. A person starts a publish, and the environment `vscode-publish` makes a second person (or the same one, by choice) approve it.
- No token is stored. `vsce publish --oidc` needs a Marketplace policy: owner `zy1233`, repository `pi-python`, workflow `vscode-extension.yml`, environment `vscode-publish`. Open VSX needs the equivalent trusted publisher in the user settings of open-vsx.org. The user creates both. Whether the very first publish of a new extension can use OIDC is unknown (report section 10); if it cannot, the first publish is a manual one by the user.
- The `package` job runs on Linux for every target. It does not run the Windows or macOS binary. Only the Linux x64 binary is started (`smoke`).
- The `smoke` step is a script of this package (`scripts/smoke-vsix.mjs`). It is written in PR 4 and is not a Python test.
- The Open VSX flags must be checked against `ovsx` at the time of use. The Marketplace flags are from the report (`vsce` 4.1.0).

## 12. Out of Phase 1

From the report, section 3.3 (Phase 2 and later), and from the limits above:

- Session list and resume (`session/list`, `resume`, `load`), and a chat history view.
- Model choice (`session/set_config_option` with `model`). Phase 1 shows the model name only.
- Slash-command completion from `available_commands_update` (a typed `/goal ...` is sent as text and the agent handles it).
- Images, `@file` and other context, `embeddedContext` blocks.
- Saving dirty files before a prompt, or an `fs/*` capability that lets the agent read the editor buffers (needs an agent change).
- MCP servers in `session/new` (the source of their configuration has to be decided).
- A universal fallback VSIX that only shows a message on an unknown platform.
- `win32-arm64`, `linux-armhf`, `alpine-*`.
- Per-hunk review in the diff, and a diff that the agent sends (section 8.1).
- `SecretStorage` for the API key.
- Code signing and notarization, verified publisher (6 months), native chat (`chatSessionsProvider`, AHP): phases 3 and 4.
- Changes to the README "Status" table and to `web/`: after the first release, and only with the user's approval.
- Any change to the agent, `release.yml` or `agent-binaries.yml`.

## 13. Test plan

**Layers.**

| Layer | What | How | Where it runs |
|-------|------|-----|---------------|
| Unit | `resolveAgentCommand`, `chmod`, version check, protocol validation, tool-call model, markdown renderer, diff maths (apply edits, reverse-apply a patch), trust-prompt detection, permission mapping | vitest | any OS |
| Fake agent | the controller and the connection against a scripted ACP agent (a Node script made with the same SDK): streaming text and thinking, a tool call with a permission card, a trust prompt, cancel with an open request, `auth_required` then setup then a second `session/new`, process exit during a turn, stderr to the log, wrong version, wrong protocol | vitest, child process | any OS with Node |
| Real agent | the same controller against `python -m pi_agent_cli` with the mock model (`PI_USE_MOCK=1`, temporary `PI_HOME`), and against `tests/_tool_agent.py` for a scripted `write` and `edit` in `ask` mode, `auto`, and a `bash` call | vitest, opt in (`ZYCODE_TEST_AGENT`, which the `npm run test:agent` script sets from `.venv`) | Linux, macOS (Windows: not run) |
| Webview | the renderer: text, thinking, tool list, bash output, permission card, no `innerHTML` from agent text, a hostile string stays text | vitest with a DOM (jsdom) | any OS |
| Extension host | activation, the view loads, `Show diff` opens a diff editor, restricted mode does not start the agent | `@vscode/test-electron` with a real VS Code, under `xvfb` on Linux | manual in Phase 1; not in CI |
| Package | `check-vsix` on each VSIX: binary, exec bit, notices, license, icon, no `node_modules`; sha256 mismatch is refused; `package` refuses on Windows | a script, run by hand now | Linux |

**Commands** (run from `packages/pi-vscode`, and pasted into each pull request): `npm ci`, `npm run typecheck`, `npm run lint`, `npm test`, and for the real agent `npm run test:agent`. No CI runs these yet: the workflow in section 11 is the draft that adds them.

**Not verified on any machine of ours:** Windows and macOS (the extension host, the terminal for `--setup`, the exec bit after a VS Code install, the first start of an unsigned binary under Gatekeeper and SmartScreen, a Windows antivirus on a PyInstaller file), Remote-SSH, WSL and dev containers, the Marketplace scan of the VSIX, Open VSX. The README and the release notes say "pre-release" for this reason.

## 14. Decisions

| # | Decision | Why |
|---|----------|-----|
| D1 | Our own ACP client and webview (route a). | The only route that works today (report section 2). |
| D2 | The core has no `vscode` import. | Tests without VS Code; a later native-chat adapter can reuse it (report section 8). |
| D3 | The webview is a plain TypeScript bundle, with no UI framework. | A small page; fewer dependencies in a package that ships a binary; less to list in the notices. |
| D4 | The extension never answers a permission or trust request for the user. | The agent's own rule (F9, `trust_prompt.py`); a wrong default here runs a project's code. |
| D5 | The extension builds the diff itself. | The agent sends no diff block (F7). The change to the agent is noted and not done here. |
| D6 | `zycode.agentPath`, `agentArgs` and `permissionMode` are `machine` and `application` settings. | A project must not choose the program or loosen the mode. |
| D7 | The permission setting wins over `agent.toml`, and `always-approve` is not offered. | One clear rule. `ask` and `auto` are both at least as strict as `always-approve`. |
| D8 | `zycodeAgentVersion` is pinned with sha256 in the repository (`agent-assets.json`). | A changed Release asset must not enter a VSIX unseen. |
| D9 | No telemetry. | User decision. |
| D10 | The publish workflow is a draft in this record, and it has no automatic trigger. | The user approves changes under `.github/workflows/` first; an agent Release is not an extension release. |
| D11 | `extensionKind: ["workspace"]`. | The agent must run where the files are. |
| D12 | The first start with no key uses `--setup` in a terminal, not `SecretStorage`. | One place for the key; the same flow as `zypi` and Zed (report section 5). |
