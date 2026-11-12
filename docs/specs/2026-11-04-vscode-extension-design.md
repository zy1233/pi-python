# zycode VS Code extension: design

> Scope: a VS Code extension (`zycode.zycode`) that runs the zycode agent and shows a chat for it. This record gives the design of Phase 1 (the pre-release 0.1.0) and of the changes of 1.0.0 (sections 7a, 7b, 9 and 11).
> Decided: 2026-11-04. The user adopted the plan of the research report (ZY-59, sections 1 to 6 and 9) and chose: route (a), an ACP-client extension with its own webview chat; publisher `zycode`; extension name `zycode`; display name "zycode"; five targets; no telemetry; no code signing; Trusted Publishing for the publish pipeline.
> Status: Phase 1 is built (`packages/pi-vscode/`, `.github/workflows/vscode-extension.yml`). 0.1.0 (pre-release) is published on both registries (uploaded by hand). 1.0.0 is the first stable release; it adds the first-run guide (section 7a), `always-approve` in the mode menu (section 9) and the Past chats view (section 7b). `packages/pi-vscode/RELEASING.md` has the steps and the 1.0.0 release record. Result (2026-11-12): 1.0.0 was published by the workflow to both registries with OIDC (section 13). Where this record and the code differ, the code wins.
> Code: `packages/pi-vscode/`. Agent side (unchanged): `packages/pi-agent-cli/pi_agent_cli/{agent,events,auth_methods,permissions,trust_prompt,permission_modes}.py`.
> Facts about the agent in this record come from the source tree at `9c763c8` and from a run of the real agent over ACP (section 1). Facts about VS Code come from the research report; they were true on 2026-11-04.

---

## 0. Summary

- **What it is.** An ACP client in an extension. It starts `zycode-agent` as a child process, talks ACP over stdio, and shows the session in a chat view.
- **Where the agent comes from.** Each platform VSIX holds the `zycode-agent` binary of one GitHub Release (`zycodeAgentVersion`, checked with sha256). The setting `zycode.agentPath` replaces it.
- **Three parts, one rule.** The *host* (extension host, Node) owns the agent process and the ACP connection. The *webview* only draws and sends messages. The *core* (ACP client, state, diff) has no `vscode` import, so it runs in unit tests without VS Code.
- **Who decides what.** The agent decides what needs a question. The extension shows the question and sends the answer. It never answers for the user.
- **Two kinds of trust.** VS Code Workspace Trust decides if the extension starts at all. The agent's own project trust decides if the project may load its extensions. The text of the two prompts says so.
- **Publishing.** Open VSX first, then the Marketplace. The first upload to each (0.1.0, a pre-release) was a hand upload of a VSIX that the packaging script made. The publish workflow (`.github/workflows/vscode-extension.yml`, section 11) runs from `main` by `workflow_dispatch`, with Trusted Publishing and the environment `vscode-publish`; it publishes the stable releases, 1.0.0 first.
- **Added in 1.0.0.** A first-run guide, `always-approve` in the mode menu (with a warning, never restored), a Past chats view (list, open, delete), and a Thinking menu. All four use what the bundled agent already has (0.1.0 bundled 0.9.0; 1.0.0 bundles **0.9.1**): no wire item and no agent change. The Thinking menu edits one line of `agent.toml` and restarts the agent, because 0.9.0 has no live setting (section 7c).
- **Not yet.** Model choice, images, `@file`, MCP servers, fallback package, and everything of report section 3.3 from Phase 2 on (section 12).

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
| F10 | `session/new` returns `modes` and the config options `model` and `mode` (values `ask` and `auto`). `always-approve` shows as the current value only if `agent.toml` sets it. **The v0.8.0 binary has no `mode`** (the probe of it showed `configOptions: ["model"]` and no `modes`). **v0.9.0 has it** (Auto is released in 0.9.0, `CHANGELOG.md`): the extension pins 0.9.0, and `scripts/smoke-vsix.mjs` checks the `mode` option on the bundled binary. | `permission_modes.py`; the spike output; `git log v0.8.0..HEAD` |
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
5. Text that the agent or the model sends is data. The webview builds the page with DOM nodes and `textContent`. It never sets `innerHTML` from agent text. A Markdown parser (`markdown-it`) reads the text and gives tokens. `core/markdown.ts` changes the tokens to plain data (blocks and inline items), and `webview/dom.ts` makes DOM nodes from that data. The HTML option of the parser is off, so raw HTML in the text stays text. The webview never loads an image: it shows an image as a link. A link opens through the host, and only `http`, `https` and `mailto` (D34).

### 2.1 Files

```
packages/pi-vscode/
├── package.json            manifest, zycodeAgentVersion, scripts
├── src/
│   ├── extension.ts
│   ├── core/               agent-process, locate, acp-connection, chat-controller, tool-calls, diff, version, protocol, markdown
│   ├── ui/                 chat-view, permission-fallback, diff-documents, setup-terminal, status-bar
│   └── webview/            main.ts, dom.ts
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

See section 9. The mode only changes which calls the agent asks about. The extension has no rule of its own: it chooses the mode, and the agent decides.

## 6. Messages between the webview and the host

All messages are JSON objects with a string `type`. The types live in one file (`core/protocol.ts`) that both sides import. The host validates every message from the webview (the webview is not trusted: a page script can send anything) and ignores unknown types.

### 6.1 Host to webview

| `type` | Fields | Meaning |
|--------|--------|---------|
| `state` | `status` (`stopped`, `starting`, `ready`, `busy`, `cancelling`, `needs-setup`, `error`, `no-folder`, `untrusted`, `unavailable`), `detail?` (text), `agent?` (`{version, source}`), `cwd?`, `mode?` (`{current, options[]}`), `model?` (text), `thinking?` (`{current?, levels[], disabled?, note?}`), `canRestart` | The full status. Sent at each change and when the view loads. |
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
| `setMode` | `value` | `ask`, `auto` or `always-approve`. The host sets the mode of the open session only (section 9); it does not write the setting. |
| `setThinking` | `value` | One of `xhigh`, `high`, `medium`, `low`, `minimal`, `off`. The host asks first, then changes `agent.toml` and restarts the agent (section 7c). |
| `runSetup` | | Run the first-run setup in a terminal (section 7). |
| `openGuide` | | Open the getting-started walkthrough (section 7a). |
| `openAgentConfig` | | Open `agent.toml` of the agent (section 7a). |
| `restartAgent` | | Section 3.4 and a start. |
| `openLog` | | Show the output channel. |
| `openLink` | `url` | The host opens it if it is `http`, `https` or `mailto`. |

There is no message that reads a file, runs a command, or sets a setting. The webview has no message for the Past chats view: it is a tree view of the host (section 7b). A notice (`notice`, `state.setup`) can carry `actions` (`runSetup`, `openGuide`, `openAgentConfig`, `openLog`): a fixed set of ids that the webview maps to the messages above.

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
- The command is also available when a session exists, to change the model or key later. It then ends with a restart of the agent and a new chat. While a setup terminal is open, a second click does nothing.
- `shellPath` with a binary and `shellArgs` makes VS Code run the binary directly in the pty. On Windows the agent binary is a console program, and this works the same. (Not verified on Windows or macOS: section 13.)

## 7a. The first-run guide (1.0.0)

**What a fresh install does today** (measured with the real 0.9.0 binary and an empty `PI_HOME`, 2026-11-12):

- No `agent.toml`: `session/new` fails with `-32000`, message "No model is set. ..." and `data.authMethods = ["pi-setup"]` (for a client that sent `auth.terminal`).
- A `[model]` table whose key variable has no value: `-32000`, "The variable NAME has no value. ...".
- A key that the provider refuses, a model id that the provider does not know, a base URL that does not answer: the session opens, and the first turn ends with an agent message `Error: <text of the provider>` (for example `Error: Error code: 401 - ...`). The agent says nothing else.
- `--setup` (the terminal login) asks for the provider, the model id, a base URL, the name of the key variable and the key (hidden). It writes the `[model]` table to `agent.toml` and the key to `local.env` (mode 0600). With a model that is already set, it asks only for the key, or whether to replace it.

**The guide uses these and adds nothing to the wire.** It is made of three parts:

1. **A VS Code walkthrough** `zycode.gettingStarted` ("Get started with zycode"; six steps: open a folder, choose a model and save a key, say hello, choose a mode, find past chats, if something does not work). Each step has a button that runs a command, and a page in `media/walkthrough/`. It opens once by itself, when the state first becomes `needs-setup` (a boolean in `globalState`: `zycode.guideShown`). It does not open by itself again. It can be opened at any time: the command `zycode.openGuide` and *Welcome: Open Walkthrough*.
2. **The banner of `needs-setup`.** `state.setup` carries `{reason: "no-model" | "no-key" | "unknown", variable?}`, read from the text of the `-32000` message (`src/core/first-run.ts`). The banner shows three steps and the buttons **Run setup** and **Open the guide**. A text that the extension does not know gives the general text.
3. **Hints for the failures that come with the first turn.** When a turn ends, the text of the last `Error: ` in the agent message is matched (a key that is refused: 401, 403, "incorrect api key"; a model that does not exist: 404, "model not found"; a server that cannot be reached: "connection error", time-out, DNS) and a notice with buttons follows: **Change the key** (the setup again), **Open agent.toml**, **Show log**, **Open the guide**. Only the text from the last `Error: ` on counts, so an answer that talks about keys gives no hint. The match is a heuristic on the text of the agent; a text that it does not match gives no hint, and the chat still shows the agent's own message.

**The key.** The key is typed in the setup terminal only, and the agent saves it in `local.env`. The extension has no input for it and does not read `local.env`. It does not log, store or show a key: no setting, no `SecretStorage`, no webview field. The pages of the guide say so, and say never to type a key in the chat. `agent.toml` holds no key, so **Open agent.toml** is safe (`PI_HOME`, with `~`, else `~/.pi-python`). If `agent.toml` does not exist, the command offers the setup.

**Not in the guide.** No new ACP method, no `_meta` key, no change in `packages/pi-agent-cli`. If the agent ever sends a structured error for a refused key, the heuristic can go.

## 7b. Past chats (1.0.0)

**What the extension had:** no history. A restart or **New chat** lost the chat for the user (the agent kept the session file).

**What the agent offers over ACP** (read in `packages/pi-agent-cli/AGENTS.md`, the register `docs/specs/2026-11-05-acp-extension-registry.md`, and run with the real 0.9.0 binary; `scripts/smoke-vsix.mjs` checks the list, load, delete and the always-approve signal again on the bundled 0.9.1 binary):

| Item | Fact |
|---|---|
| `session/list` | Advertised in `agentCapabilities.sessionCapabilities.list`. Takes `cwd` (exact match; the agent filters) and a cursor. 50 rows a page, `nextCursor`. Newest first. A row has `sessionId`, `cwd`, `title` (the first prompt) and `updatedAt`. A session appears after its first message. |
| `session/load` | Advertised (`loadSession`). The agent replays the history as `session/update` notifications with `_meta.isReplay: true` before the response. The response has `_meta["pi/cwd"]`, the folder that the session was saved in. |
| `session/close` | Used for the chat that is left. |
| `pi/session/delete` (`_pi/session/delete` on the wire; register item X1) | Not advertised. Idempotent. An agent without it answers `-32601`. |

Nothing else is needed, so there is **no wire item to add and no agent change**.

**The precedent (the pager, `tui/crates/codegen/pi-pager`):** a row is the title and the time ("5m ago"); the order is the agent's order; a delete is armed first and then confirmed; a missing title falls back to a short id. The extension follows this.

**Where it is shown: a tree view `zycode.sessions` ("Past chats") under the chat, in the same activity bar container.** Reasons: VS Code gives scrolling, keyboard use, empty and error messages (`TreeView.message`), a title bar with buttons and an inline button for each row, with no code of our own in the webview; the chat keeps its width and its transcript; the list needs no message type in the webview protocol, and so it cannot be reached by agent text; a list inside the chat would need all of that in the webview and would push the transcript down. The cost: the user sees two views in one container. Use the view `Past chats` title bar for **New chat** and **Refresh**.

**Rows and states** (`src/core/session-list.ts`, `src/ui/sessions-view.ts`):

- Row: the title (one line, at most 120 characters, else `Chat <first 8 characters of the id>`), the time of the last message ("just now", "5m ago", "3h ago", "2d ago", "4mo ago"), a marker (`open`) on the open chat. The tooltip has the full time, the folder and the id.
- The list is read at start and after each turn, after **New chat**, after an open and after a delete, and a minute-timer repaints the times. Reads that overlap make one more read after the first.
- States: `loading`, `empty` ("No past chats in this folder yet. A chat appears here after its first message."), `error` (the text of the error), `unavailable` (no folder, Restricted Mode, the agent is not running, the agent has no `session/list`), and `ready`. A list that has more than 20 pages (1000 chats) shows the newest 1000 and says so.
- **Open** (click): only a row of the list can be opened, and only while the state is `ready` (not during an answer or while the setup runs). The extension closes the open session, calls `session/load` with the folder of the window, and shows the replayed user messages and answers (replayed `user_message_chunk` only with `_meta.isReplay`; replayed tool calls get no diff buttons, because the files may have changed). The mode is set by the rule of section 9, as for a new session.
- **Delete** (the trash button, or the context menu): a modal question names the chat and says that it cannot be undone. Then `_pi/session/delete`. If the open chat is deleted, a new chat starts. An agent that answers `-32601` gets one notice, "This agent version cannot delete a chat", and the list shows no trash button.
- **New chat**: as before; the list shows the new chat after its first message.

**A chat of another folder never shows in the wrong workspace.** Three checks. (1) `session/list` is called with the folder of the window. (2) Each row is checked again with `sameFolder` (a trailing separator is ignored; on Windows the letter case and the kind of slash are too), so an agent that ignores the filter cannot put a foreign row in the list. (3) After `session/load`, `_meta["pi/cwd"]` must be the same folder, else the session is closed, nothing is shown, and a notice says "That chat belongs to another folder". The list is not read in Restricted Mode, and the agent is not started there (project trust, invariant 9 of `AGENTS.md`). The extension reads no session file itself.

## 7c. Thinking level (1.0.0)

**What the extension had:** no way to see or change the thinking level. `zycode.showThinking` only decides whether thinking blocks are shown.

**What agent v0.9.1 offers** (read in the agent source, `packages/pi-agent-cli/AGENTS.md` and the register `docs/specs/2026-11-05-acp-extension-registry.md`, and measured on the 0.9.0 and 0.9.1 binaries and on the Python agent against a fake model server):

| Question | Fact |
|---|---|
| Where does the level live? | Only in `agent.toml`: the top-level key `thinking_level`. No ACP config option (the options are `model` and `mode`), no register item, no environment variable, no command-line flag. |
| Which levels? | `off`, `minimal`, `low`, `medium`, `high`, `xhigh` (`_VALID_THINKING` in `config.py`). A missing or invalid value is `off`. The agent does not list levels for a model: the six are the same for every model. |
| How does the agent say that a model has no thinking? | It does not say it over ACP. `Model.reasoning` is `reasoning` in `[model]` (or at the top level) when that key is set, else `thinking_level != "off"` (`CliConfig.model_reasoning`). The adapter asks a provider to think only if `Model.reasoning` is true and the level is not `off` (invariant 5 of `AGENTS.md`). So `reasoning = false` in the file is the one signal. |
| Can it change in a live session? | **No.** The agent reads `thinking_level` when the process starts (`factory.py` gives it to the harness). The harness has `set_thinking_level`, but the ACP agent never calls it. Measured with the 0.9.0 binary and again with the 0.9.1 binary (the same answers; 0.9.1 only changed the adapter of the openai provider, #121): `session/new` has no thinking option and no related `_meta`; `session/set_config_option` with a thinking id answers `-32602`; `_pi/set_thinking_level` and `pi/set_thinking_level` answer `-32601`. The agent reads `agent.toml` again only while it has a configuration problem (F12), so an edit does not reach a running agent. |
| What does the pager (`zypi`) offer? | Nothing for this agent. It refuses `--reasoning-effort` and `--effort` and says "The Python agent gets no reasoning level from the client. Set thinking_level in agent.toml." The `/model` effort menu needs the model metadata `supportsReasoningEffort` of another agent, which the Python agent does not send. Its fallback menu lists `xhigh`, `high`, `medium`, `low` with the descriptions "Extended reasoning", "Heavy reasoning", "Balanced reasoning", "Faster, lighter reasoning". The extension follows these names and this order (strongest first) and adds the two levels that the Python agent also takes, `minimal` and `off`. |

**So no live switch is possible without an agent change.** The extension adds no wire item and changes nothing in the agent. The only input that the agent has is the file, and the agent reads it at its start. What the extension does:

1. **Shows** the level that the running agent uses: the extension reads `agent.toml` just before it starts the agent (`readThinking`, a TOML parser; `smol-toml`, BSD-3-Clause, bundled and named in `THIRD-PARTY-NOTICES`). The menu has the six levels. It shows nothing until the agent is up.
2. **Disables** the menu, with a reason in its tooltip, when: `reasoning = false` is set (the model does not think); an answer is running; `agent.toml` does not exist (the setup creates it) or is not valid TOML. A `thinking_level` that the agent does not know is shown as `off` with a note, as the agent reads it.
3. **Changes** the level in four steps, from the state `ready` only: (a) a modal names the file and the line, and says that the agent restarts, that the chat opens again and that the level then applies to every chat and to `zypi`; (b) the extension edits `agent.toml` (below); (c) it stops the agent and starts it again; (d) it opens the same chat with `session/load` (the agent replays the history). A chat with no message is not loaded: a new chat opens. Always approve is off again after the restart, and the notice says so. If the chat cannot be loaded, the existing path opens a new chat with a notice.
4. **The edit** (`setThinkingLevelInText`, `writeThinkingLevel`): it replaces the string of an existing top-level `thinking_level` line (comment, spacing and line ends stay), or adds the line above the first table header (above the comment lines that belong to the header; at the end if the file has no table). It is a text edit, so it is checked: both texts are parsed, and the new one must be the old one with only `thinking_level` changed. If the check fails, or the key is written in a form that the edit does not know (an array, a dotted key, an inline table), the file is not touched and a notice says to edit the line by hand. The file is written beside the file and moved over it (a symbolic link is followed, the mode is kept). The extension never creates `agent.toml`, and it never touches `local.env` (the key).

**What a new chat, a past chat and a restart start with.** The level is in `agent.toml`, so all of them start at the level that the file has, as `zypi` does. The agent does not save a level in the chat (the harness has a `thinking_level_change` entry, but the ACP agent never writes it, and the load does not read it), so a past chat runs at the level of the running agent, and a chat has no level of its own. There is no setting `zycode.thinkingLevel`: it would be a second source that can differ from the file, and the agent cannot take it per session. See decisions D28 to D33.

**Tested against a fake model server** (`test/real-thinking.test.ts`, opt in, Python agent and the 0.9.0 and 0.9.1 binaries): at `low` the model request has `reasoning_effort: "low"`; after a change to `high` the agent restarts, the chat comes back with its history, and the next request has `reasoning_effort: "high"` and the earlier question; at `off` the request has no thinking parameter; a model with `reasoning = false` has no menu, and the request has no thinking parameter at `high`; a new controller (a restart of VS Code) starts at the level of the file. `test/thinking.test.ts` runs the same flows against the fake ACP agent, and the edit and the file write on their own.

**Not verified:** a real provider. The agent passes `reasoning_effort` (the OpenAI-format provider) and the provider's own parameters for the others; the extension does not know which model of which provider accepts a level.

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
| `zycode.permissionMode` | `"ask"` or `"auto"`, `"ask"` | `application` | The mode of a start: of every new session, a restart and a past chat that is opened. It cannot be `always-approve`. |
| `zycode.showThinking` | boolean, `true` | `window` | Show the thinking blocks (collapsed). |

**Scopes are a security choice.** A setting that names a program, or loosens permissions, must not come from the folder that the agent works in. `machine` and `application` settings are read from the user's settings only, never from `.vscode/settings.json` or a `.code-workspace` file. Workspace Trust already stops the extension in an untrusted folder, but a trusted folder is trusted to run its tasks, and not to choose the agent.

**Mode.** `ask` and `auto` are the two values the agent lets a client choose (F10). After `session/new`, if the response has a config option with id `mode` (category `mode`) and its current value is not the setting, the controller sends `session/set_config_option`. The setting wins over `permission` in `agent.toml`, **including `always-approve`**: a session starts in `ask` or `auto`, never in `always-approve`. A value `always-approve` in a hand-edited `settings.json` is read as `ask`. A change of the setting applies to the open session at once. The status shows the mode.

**Always approve in the mode menu (1.0.0).** The menu of the chat shows `ask`, `auto` and, when it is allowed, `always-approve`. What the pager and the agent do: the standard `mode` option of the agent takes `ask` and `auto` only (decision D6 in `packages/pi-agent-cli/AGENTS.md`), and a live session reaches `always-approve` only through the agent's own notification `pi/yolo_mode_changed` (`_pi/yolo_mode_changed` on the wire, register item X8; the pager's `Ctrl+Y` sends it, without a question). The extension does the same and adds a question:

1. **Offered** only if the workspace is trusted, the agent has a `mode` option (v0.9.0 and later), and the user did not refuse the agent's project-trust question in this agent process. Otherwise the menu has two options, and a call for it says why it is not available.
2. **Warning first.** A modal (`showWarningMessage`, `modal: true`): "Turn on Always approve?" — every tool call, `bash` included, then runs with no question; it stays on for the open chat only; a new chat, a past chat and a restart start in Ask. No button other than the one that turns it on and Cancel. The mode does not change on Cancel or when the modal is closed.
3. **The switch.** The extension sends `_pi/yolo_mode_changed {permission_mode: "always-approve"}` and waits (5 s) for the agent's `config_option_update` that reports it. The view shows what the agent reports: if the agent does not confirm, the mode stays what it was and a notice says so. It never calls `set_config_option` with `always-approve`.
4. **A visible label** while it is on: a warning chip "Always approve: no questions" in the header, a warning border on the header and the composer, and the status bar item (warning colour, the words "always approve", a line in the tooltip).
5. **Never restored.** Before a new chat, a past chat that is opened, or the deletion of the open chat, the extension puts the mode of the setting back (`set_config_option`, then `_pi/yolo_mode_changed` with that mode, because the agent keeps its last mode as the start mode of later sessions). A restart of the agent (or of VS Code) starts a new agent process, and `applyPermissionSetting` runs after each `session/new`. So a clean start is always `ask` (or `auto` by the setting), and no state of `always-approve` is saved anywhere.
6. **Taken away** when the user refuses (or cancels) the agent's project-trust question: the mode goes back to the setting, the option leaves the menu until the agent is restarted, and a notice says so. The menu offers no `always-approve` while `isTrusted` is false (VS Code also stops an extension that does not support Restricted Mode).
7. **What it does not change.** The agent's own questions that no mode removes still show (the project-trust question, section 5.2), and `always-approve` does not make the extension answer them (D4). Headless and `zypi -p` runs are not touched.

`always-approve` is not a sandbox either: the README says what it does and does not do.

When the response has no `mode` option (an agent older than v0.9.0 that `zycode.agentPath` points to, F10), the extension cannot set it. The agent is then in `ask` (the default of `agent.toml`) or in whatever `agent.toml` says. The view hides the mode switch, shows "mode: set by agent" and, if the setting is `auto`, one notice: "This agent version has no Auto mode."

**The README says** (what the agent's own notes say, and no more): Auto guards what a call writes or runs; it does not guard what a call reads or sends, and it is not a sandbox.

**Status.** One status bar item (`zycode: ready | busy | setup | error`, mode in the tooltip, and the words "always approve" with a warning colour while that mode is on) on the left. A click focuses the chat. The chat header shows the same status, the agent version and source, the model name from the `model` option (read only in Phase 1), and the mode.

**Workspace Trust and virtual workspaces.** `capabilities.untrustedWorkspaces = {supported: false, description: "zycode runs an agent that reads and writes files and runs commands in your folder."}` and `capabilities.virtualWorkspaces = {supported: false, description: "..."}`. VS Code then does not activate the extension in Restricted Mode. As a second guard the core checks `workspace.isTrusted` before the start, and the view shows `untrusted` if it is false (for example after the user turns the extension on by hand through a path that VS Code allows). `onDidGrantWorkspaceTrust` starts the flow again. This is VS Code's question. It is a different question from the agent's project trust (section 5.2), and the README says so in one short paragraph.

**Telemetry.** None. The extension sends nothing to the authors. It has no telemetry code, no analytics package, no update ping. The README has a privacy section: the agent sends the conversation, and the files it reads, to the model service that the user configured in `agent.toml`; the extension and the authors see none of it. `isTelemetryEnabled` is not used because nothing is sent.

## 10. Commands

| Command | Does |
|---------|------|
| `zycode.openChat` | Focus the chat view. Start the agent if it is stopped. |
| `zycode.newChat` | A new session. |
| `zycode.stop` | Stop the turn. |
| `zycode.restartAgent` | Stop and start the agent. |
| `zycode.runSetup` | Section 7 ("Set up model and API key"; also to change the key later). |
| `zycode.openGuide` | Open the walkthrough (section 7a). |
| `zycode.openAgentConfig` | Open `agent.toml` (section 7a). |
| `zycode.refreshSessions` | Read the list of past chats again (section 7b). |
| `zycode.openSession`, `zycode.deleteSession` | Used by the Past chats view (not in the Command Palette). |
| `zycode.showLog` | Show the output channel. |

Activation events: `onView:zycode.chat`, `onView:zycode.sessions`, `onCommand:*` of the commands above. Not `*`: the extension does not start when VS Code starts. The view is in its own activity bar container with the zycode icon (a monochrome SVG for the container; the PNG is the marketplace icon).

## 11. Packaging and version pinning

**Version rules.** Extension version `1.0.0`: a stable release, with an even minor. The first release, `0.1.0`, was a pre-release (an odd minor, `vsce package --pre-release`). The Marketplace takes `major.minor.patch` only. The rule of the Marketplace and of VS Code: releases use an even minor and pre-releases an odd minor, and the two lines are separate; a user of the pre-release gets the highest version of both, so `1.0.0` replaces `0.1.0` for them. From 1.0.0 on the package has no pre-release flag (`scripts/package.mjs` calls `vsce package` without `--pre-release`, the workflow calls `vsce publish` without it, `check-vsix` fails a VSIX that has it). The next pre-release line is `1.1.x`, the next stable release `1.2.0`; a pre-release needs the flag and a workflow input again, in its own pull request. `engines.vscode` is `^1.90.0` (1.90 has the webview and terminal API that the design uses). The extension version and the agent version are separate: `package.json` has `zycodeAgentVersion` (`"0.9.0"` in the first release).

**The pin.** `agent-assets.json` holds, for the pinned Release tag, one entry for each target: asset name, byte size and sha256. The values are copied from the Release once, by a person, and reviewed in a pull request. The script does not trust the Release page for them: it checks the downloaded file against the file in the repository. A different sha256 on the Release is a failure, not an update.

| `vsce --target` | Release asset | In the VSIX |
|-----------------|---------------|-------------|
| `linux-x64` | `zycode-agent-linux-x86_64-<v>.tar.gz` | `bin/zycode-agent` |
| `linux-arm64` | `zycode-agent-linux-aarch64-<v>.tar.gz` | `bin/zycode-agent` |
| `darwin-arm64` | `zycode-agent-darwin-aarch64-<v>.tar.gz` | `bin/zycode-agent` |
| `darwin-x64` | `zycode-agent-darwin-x86_64-<v>.tar.gz` | `bin/zycode-agent` |
| `win32-x64` | `zycode-agent-windows-x86_64-<v>.zip` | `bin/zycode-agent.exe` |

No `alpine-*` (the binary needs glibc), no `linux-armhf` (no binary), no `win32-arm64` (not tested).

**Local script** (`scripts/package.mjs <target>|all`): check `zycodeAgentVersion == agent-assets.json version`; download the asset (HTTPS from GitHub Releases) to a cache folder; compare size and sha256 with the pin; extract the one file; set mode `0755` (POSIX targets); run the build (esbuild bundles host and webview); generate `THIRD-PARTY-NOTICES`; run `vsce package --target <t> --out out/zycode-<t>-<version>.vsix`; run `check-vsix`; write `out/SHA256SUMS`, and print the path, size and sha256 of each file, so that a person can upload a file by hand (see "Publishing order and the first upload"). The check opens the VSIX and verifies: the binary is there, with the exec bit (the bit survives only when packaging on Linux, so the script refuses to package on Windows); `LICENSE`, `THIRD-PARTY-NOTICES` and the PNG icon are there; there is no `node_modules`, no source map, no `.env`; the manifest has the right target and no pre-release flag; every page of the walkthrough is in the package.

**`THIRD-PARTY-NOTICES`.** One file with two parts. Part 1: the Python distributions in the agent binary. The script makes a clean virtual environment, installs the same packages as the agent build (`pi-agent-cli-lc` at `zycodeAgentVersion` and its extras that `packaging/agent/zycode-agent.spec` includes), and reads name, version, license and license files from each distribution's metadata (`importlib.metadata`). It adds a fixed entry for CPython (PSF-2.0) and for PyInstaller's bootloader (GPL-2.0 with the bootloader exception), and a line for the native libraries that CPython links in. Part 2: the npm packages that esbuild bundled into `dist/extension.js` and `dist/webview.js` (from the two esbuild metafiles), with their license files. The last `node_modules/` in the path of a file names its package. So a package that sits inside another package (`entities`, inside `markdown-it`) is listed too. A distribution with no license text is listed with a note, and the script fails the build if a license is "UNKNOWN" and is not on a reviewed allow list. The file is not committed.

**Other files.** `LICENSE` is a copy of the repository's `LICENSE` (MIT). The README first screen says "unofficial; not affiliated with pi or Microsoft". The README also says: the binary is not code-signed; it holds only the OpenAI-format provider; Linux needs glibc 2.35 or newer; the first start takes a few seconds; Auto is not a sandbox. `.vscodeignore` leaves out everything that is not `dist/`, `bin/`, `media/`, `package.json`, the README, the CHANGELOG, the LICENSE and the notices.

**Publishing order and the first upload.** Facts from the user-side setup guide (`vscode-publishing-setup-guide.md`, sections 0.1, A, 3.1 to 3.3, C) and from the documents and sources named below (read 2026-11-12):

1. **Open VSX first, then the Marketplace.** Open VSX holds a new extension "under review" when an extension of the same name exists on the Marketplace and the publisher is not the owner of the Open VSX namespace. (The user owns the namespace `zycode`, so this is a safeguard, and the order stays.)
2. **The Open VSX trusted publisher is registered for one extension, and only after that extension exists and is active** (Open VSX wiki, "Trusted Publishing"). So the first upload to Open VSX is a hand upload. The owner said on 2026-11-12 that the registration exists. No workflow run had used it when this was written; the 1.0.0 run did (Result below). The Open VSX web page takes a `.vsix` (the page queues several files; the server reads `TargetPlatform` and the pre-release property from the VSIX manifest, so a platform VSIX and its flag survive; `ovsx` and the page use the same publish endpoint).
3. **The Marketplace.** `vsce publish` looks the extension up and creates it when it is not found (`publish.ts`). No document says that this works with `--oidc`, so the first Marketplace publish does not depend on it. The Marketplace web page "New extension" takes a VSIX; whether it takes a platform VSIX is not documented (not verified). So the first Marketplace upload is **one** VSIX (`linux-x64`) by hand, and the workflow publishes the other four through the update path (the path that documented OIDC examples use).
4. **The environment `vscode-publish` allows deployments from `main` only** (checked through the API: one branch rule `main`, no tag rule, reviewer `zy1233`, self-review allowed (the user is the only reviewer), administrators cannot bypass, no secret). The publish job runs from `main`. A run from another branch or from a tag is refused by GitHub.
5. **Commands and permissions.** `vsce publish --oidc` (`vsce` `^4.1.0` and the lock file; `--oidc` conflicts with `--pat` and `--azure-credential`) and `ovsx publish --trusted-publishing` (`ovsx` 1.4.0, exact); the job has `permissions: id-token: write` and `environment: vscode-publish`. Both registry entries name the workflow by file name only: `vscode-extension.yml`. They use the same repository (`zy1233/pi-python`, owner id 40223340, repository id 1246454160, as the Marketplace policy shows) and the same environment.
6. **Details that the workflow follows.** `ovsx publish <file>` ignores `--pre-release` for a prepackaged file (it warns): the flag is in the VSIX manifest. `vsce publish --packagePath <file>` takes the flag from the VSIX manifest; the workflow no longer passes `--pre-release`, and `check-vsix` fails a VSIX that has the flag. `vsce publish --packagePath` cannot be combined with `--target`, so the workflow publishes the VSIX files that the package job made. Each `vsce` call asks for a new OIDC token (a token can be exchanged once). `--skip-duplicate` on both makes a repeat of a half-done publish safe. The Open VSX `publisher` must have the same case as the namespace (`zycode`). The Marketplace policy is "Active" with no 48-hour rule, because the repository is public. No document names a limit for a Marketplace publisher on a personal Microsoft account.

What this means for the package:

- The packaging script makes VSIX files that a person can upload by hand: `zycode-<target>-<version>.vsix` in `packages/pi-vscode/out/`, each built as a stable release (since 1.0.0), plus `SHA256SUMS`. A file is the same whether the workflow or a person uploads it. The workflow also keeps the five VSIX files as run artifacts for 14 days, so a person can take them from a run with `publish` off.
- `packages/pi-vscode/RELEASING.md` is the exact checklist of the first release: (1) the five VSIX files by hand to Open VSX; (2) register the Open VSX trusted publisher (done, by the owner's word of 2026-11-12), and test it with a workflow run that only skips duplicates (no such run was made); (3) `linux-x64` by hand to the Marketplace, then the workflow for the other four targets; (4) later releases from the workflow.
- Not verified: the live Open VSX page with five files at once; that the Marketplace page keeps the pre-release flag of a VSIX and takes a platform VSIX; the first `vsce publish --oidc` of any kind from this repository. The checklist says what to look at after each step.
- **Result (2026-11-12): the first `vsce publish --oidc` and the first Open VSX Trusted Publishing publish from this repository worked.** Run `43181052994` on `main` (commit `8b0e50c`) published 1.0.0 for five targets to Open VSX (08:18:55Z to 08:19:17Z, `Trusted publishing token issued`) and then to the Marketplace (`vsce publish --oidc`, 08:19:24Z to 08:19:57Z), with no token. The run finished green at 08:20Z. Both versions were visible with the public APIs: Open VSX 08:23Z to 08:25Z, Marketplace 08:26Z to 08:28Z. `preRelease` is false on Open VSX, the Marketplace shows no PreRelease property, and the sha256 of the five Open VSX files equals the `sha256sum` in the Publish log. Record: `packages/pi-vscode/RELEASING.md`.

**Which trigger runs on `main`.** `workflow_dispatch` runs on the branch or tag that the user chooses. The "Run workflow" button offers any branch, and the environment rule refuses all but `main`. A `workflow_run` run always uses the default branch: GitHub sets `GITHUB_REF` to the default branch and `GITHUB_SHA` to its last commit (GitHub Docs, "Events that trigger workflows", `workflow_run`), so it passes the environment rule, but it builds the last commit of `main`, not the commit of the run that triggered it. A tag or `release` trigger would run on a tag ref, which the environment refuses. The extension version is not the agent version, and a new agent Release must not publish an extension by itself. So the workflow uses `workflow_dispatch` only for publishing. The job `guard` fails at once, before any build and before the reviewer is asked, when `publish` is ticked on a ref other than `main`.

**The workflow** is `.github/workflows/vscode-extension.yml`. On a pull request that changes the extension it runs `npm run check` and builds and smoke-tests the `linux-x64` VSIX. On `workflow_dispatch` it does the same for the chosen targets (default: all five), and with `publish` ticked the job `publish` runs in the environment `vscode-publish`: Open VSX first, then the Marketplace (`registries` can select one of them, for a repeat). Top-level permissions are `contents: read`; only `publish` has `id-token: write`. It has no secret and no `workflow_run`, `push`, `release` or tag trigger (`test/packaging.test.ts` checks these). It publishes stable releases (1.0.0 and later), because `scripts/package.mjs` packs with no pre-release flag. 0.1.0 was published by hand; 1.0.0 was the first real Trusted Publishing publish (it worked on the first try, 2026-11-12; see the Result in section 13), and `RELEASING.md` has the fallback if OIDC fails (run again, then a hand upload from the run artifacts, a token only as the last resort, revoked at once).

## 12. Not yet (after 1.0.0)

From the report, section 3.3 (Phase 2 and later), and from the limits above:

- A live thinking level (no restart, a level for each chat, a list of levels for each model). It needs an agent change: a `thinking_level` config option on the session (the standard Session Config Options, like `model` and `mode`) that calls the harness `set_thinking_level` and is saved in the chat, and a way to say that a model has no thinking. Decision D28 (option C).
- Past chats beyond the list of section 7b: search, rename, grouping by date, a list of the chats of other folders, `session/resume` (the extension uses `session/load`).
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
| Unit | `resolveAgentCommand`, `chmod`, version check, protocol validation, tool-call model, Markdown reader (fixed cases, hostile texts, 3000 random mixes of marks with a fixed seed), diff maths (apply edits, reverse-apply a patch), trust-prompt detection, permission mapping | vitest | any OS |
| Fake agent | the controller and the connection against a scripted ACP agent (a Node script made with the same SDK): streaming text and thinking, a tool call with a permission card, a trust prompt, cancel with an open request, `auth_required` then setup then a second `session/new`, process exit during a turn, stderr to the log, wrong version, wrong protocol; since 1.0.0 also: `session/list` (pages, other folders), `session/load` with replay, `_pi/session/delete` (and `-32601`), `_pi/yolo_mode_changed`, refused keys, models and servers | vitest, child process | any OS with Node |
| Real agent | the same controller against `python -m pi_agent_cli` with the mock model (`PI_USE_MOCK=1`, temporary `PI_HOME`), and against `tests/_tool_agent.py` for a scripted `write` and `edit` in `ask` mode, `auto`, and a `bash` call; and (`test/real-first-run.test.ts`) against the fake model server of `packaging/agent/smoke.py` with a clean `PI_HOME`: needs-setup, the real `--setup` typed in a pseudo-terminal, a first answer, a refused key and its replacement, a wrong base URL, and the session list (new, load with replay, delete, a restart of the extension, another folder); and (`test/real-thinking.test.ts`) the thinking level in the request that a fake model server logs (a level, a change with the same chat opened again, `off`, a model with `reasoning = false`, a restart) | vitest, opt in (`ZYCODE_TEST_AGENT`, which the `npm run test:agent` script sets from `.venv`) | Linux, macOS (Windows: not run) |
| Webview | the renderer: text, thinking, tool list, bash output, permission card, Markdown (headings, emphasis, lists, a quote, a rule, a table with alignment), no `innerHTML` from agent text, a hostile string stays text | vitest with a DOM (jsdom) | any OS |
| Extension host | activation, the view loads, `Show diff` opens a diff editor, restricted mode does not start the agent | `@vscode/test-electron` with a real VS Code, under `xvfb` on Linux | manual in Phase 1; not in CI |
| Package | `check-vsix` on each VSIX: binary, exec bit, notices, license, icon, walkthrough pages, no pre-release flag, no `node_modules`; sha256 mismatch is refused; `package` refuses on Windows. `scripts/smoke-vsix.mjs` starts the frozen binary three times (section PACKAGING.md): no model, the mode option, and a turn with a fake model server for the list, load, delete and the always-approve signal | a script; in CI for `linux-x64` | Linux |

**Commands** (run from `packages/pi-vscode`, and pasted into each pull request): `npm ci`, `npm run typecheck`, `npm run lint`, `npm test`, and for the real agent `npm run test:agent`. The workflow in section 11 runs the same checks on a pull request that changes the extension.

**Result (2026-11-12): the Open VSX and Marketplace publish path is now measured (1.0.0, five targets, OIDC, no token); an install of 1.0.0 in a real VS Code is not.** **Not verified on any machine of ours:** Windows and macOS (the extension host, the terminal for `--setup`, the exec bit after a VS Code install, the first start of an unsigned binary under Gatekeeper and SmartScreen, a Windows antivirus on a PyInstaller file), Remote-SSH, WSL and dev containers, the Marketplace scan of the VSIX, Open VSX. The README lists the limits that remain.

## 14. Decisions

**Status of D15 to D33:** the user adopted all of them as recommended (2026-11-12, 14:49 Asia/Shanghai: "都按你的建议", one answer for every decision). Each row marked (adopted) is built as the row says. The bundled agent of 1.0.0 is **0.9.1** (`agent-assets.json`, pinned with size and sha256).

**Status of D34:** D34 is newer (2026-11-12, after 1.0.0). It is the answer of the user to a question with three options: the user chose option A.

| # | Decision | Why |
|---|----------|-----|
| D1 | Our own ACP client and webview (route a). | The only route that works today (report section 2). |
| D2 | The core has no `vscode` import. | Tests without VS Code; a later native-chat adapter can reuse it (report section 8). |
| D3 | The webview is a plain TypeScript bundle, with no UI framework. | A small page; fewer dependencies in a package that ships a binary; less to list in the notices. |
| D4 | The extension never answers a permission or trust request for the user. | The agent's own rule (F9, `trust_prompt.py`); a wrong default here runs a project's code. |
| D5 | The extension builds the diff itself. | The agent sends no diff block (F7). The change to the agent is noted and not done here. |
| D6 | `zycode.agentPath`, `agentArgs` and `permissionMode` are `machine` and `application` settings. | A project must not choose the program or loosen the mode. |
| D7 | The permission setting wins over `agent.toml`, and a session never starts in `always-approve` (since 1.0.0 the user can choose it in the chat menu, section 9; D17 to D20). | One clear rule for a start. `ask` and `auto` are both at least as strict as `always-approve`. |
| D8 | `zycodeAgentVersion` is pinned with sha256 in the repository (`agent-assets.json`). | A changed Release asset must not enter a VSIX unseen. |
| D9 | No telemetry. | User decision. |
| D10 | The publish workflow is `.github/workflows/vscode-extension.yml`, and it publishes only by `workflow_dispatch` from `main`. | An agent Release is not an extension release; the environment `vscode-publish` allows `main` only. |
| D13 | Open VSX first, then the Marketplace. | Open VSX holds a new extension under review when the same name exists on the Marketplace and the publisher is not the namespace owner. |
| D14 | The first upload to Open VSX is a hand upload of the five VSIX files. The first Marketplace upload is a hand upload of `linux-x64`, and the workflow publishes the other four. The workflow publishes every later release. | Open VSX Trusted Publishing needs an existing, active extension; the first Marketplace publish with OIDC for a new extension is undocumented; a platform VSIX on the Marketplace web page is undocumented. |
| D11 | `extensionKind: ["workspace"]`. | The agent must run where the files are. |
| D12 | The first start with no key uses `--setup` in a terminal, not `SecretStorage`. | One place for the key; the same flow as `zypi` and Zed (report section 5). |
| D15 (adopted) | The first-run guide is a walkthrough, a banner with steps, and hints after a failed first turn. It opens once by itself. It adds no wire item and changes nothing in the agent (section 7a). | The agent already says what is missing (`auth_required`, `--setup`); a person who has never seen the agent needs the steps in the place where the chat stops. |
| D16 (adopted) | The hints read the text of the last `Error: ` message of the agent (regular expressions), and say nothing when they do not match. | The agent has no structured error for a refused key (measured on 0.9.0). A wrong hint is worse than none. |
| D17 (adopted) | `always-approve` is in the mode menu as the third option, and the extension reaches it with the agent's own `pi/yolo_mode_changed` (X8), never with `set_config_option`. | The `mode` option takes `ask` and `auto` only (D6 of the agent). The pager uses the same signal. |
| D18 (adopted) | A modal warning comes before the switch; there is a visible label while it is on; it is not offered in an untrusted workspace and not after a refused project-trust question. | One click must not turn off every question. Invariant 9 (project trust). |
| D19 (adopted) | A clean start is always `ask` (or `auto` by the setting): a new chat, a past chat and a restart never keep `always-approve`, and the extension puts the agent's start mode back. | The agent keeps its last mode for later sessions (X8); the extension must not leave it behind. |
| D20 (adopted) | The setting `zycode.permissionMode` stays `ask` or `auto`. | A setting would restore the mode at each start, against D19. |
| D21 (adopted) | Past chats is a tree view under the chat, not a list inside the chat (section 7b). | Native scrolling, keyboard use, messages and buttons; nothing new in the webview; the transcript keeps its room. |
| D22 (adopted) | The order and the row come from the agent and follow the pager: newest first, the first prompt, the time. | One order in `zypi` and in VS Code; the extension reads no session file. |
| D23 (adopted) | A delete asks first in a modal and uses `pi/session/delete` (X1); an agent without it shows no delete. | A delete cannot be undone. |
| D24 (adopted) | A chat of another folder never shows or opens: the folder filter of `session/list`, a second check of each row, and the `pi/cwd` of `session/load`. | The history of one folder must not appear in another (project trust, invariant 9). |
| D25 (adopted) | The version is `1.0.0`, stable (even minor). The next pre-release line is `1.1.x`, the next stable release `1.2.0`. | The Marketplace rule for the two lines; `0.1.0` is a pre-release, and `1.0.0` is above it. |
| D26 (adopted) | If the OIDC publish of 1.0.0 fails: read the log, run again, then upload the VSIX of the run artifacts by hand; a token only as the last resort. | 1.0.0 is likely the first real OIDC publish; both registries took 0.1.0 by hand. Result (2026-11-12): the OIDC publish of 1.0.0 worked on the first try on both registries, so the fallback was not used. The decision stays for a later run that fails. |
| D27 (adopted) | `smoke-vsix.mjs` also checks the list, load, delete and the always-approve signal on the frozen binary of each release; the vitest tests of the real agent stay opt-in. | The frozen binary is what ships; CI has no Python environment for the extension. |
| D28 (adopted) | **Recommended: as built (option B).** The Thinking menu edits `thinking_level` in the user's `agent.toml`, restarts the agent, and opens the same chat again. Options: **A**, a read-only label plus the **Open agent.toml** button (the extension writes nothing; the user edits the file and presses Restart); **B**, as built (one verified line edit, after a modal); **C**, an agent change (a `thinking_level` session config option that calls `set_thinking_level` and is saved in the chat). C is the right long-term answer and needs the user's word. | Agent 0.9.0 has no live setting (section 7c). B gives the user the switch that was asked for, with the one input that the agent has. It writes a file of the user, so the modal names the file, the line and the restart. A is the zero-write fallback: remove the write path and keep the label. |
| D29 (adopted) | **Recommended: as built.** No setting `zycode.thinkingLevel`. A new chat, a past chat, a restart of the agent and a restart of VS Code start at the level that `agent.toml` has. | The file is the one source, and `zypi` uses it too. A setting would be a second source that can differ from the file, and the agent cannot take a level for one session. The agent does not save a level in a chat, so a past chat has no level of its own. |
| D30 (adopted) | **Recommended: as built.** The menu has the six levels of the agent (`xhigh`, `high`, `medium`, `low`, `minimal`, `off`), in the order of the `zypi` menu (strongest first), with the names and the descriptions of `zypi`. The list is the same for every model. | The agent lists no levels for a model; the pager's fallback menu is the only precedent. `minimal` and `off` are accepted by the agent. |
| D31 (adopted) | **Recommended: as built.** A model without thinking is a model with `reasoning = false` in `agent.toml`: the menu is off and its tooltip says why. With `reasoning` unset the menu is on. | The agent does not say it over ACP, and `reasoning` is the agent's own switch (`Model.reasoning`). A guess from the model id would be wrong for gateways. |
| D32 (adopted) | **Recommended: as built.** The edit changes one top-level line, keeps comments and line ends, is checked by parsing both texts (`smol-toml`, BSD-3-Clause, a build-time dependency that is bundled), refuses a form it does not know, and writes through a temporary file. A modal asks first. | A wrong edit of a config file is worse than no edit. A regular expression alone is a guess about the layout; the parse of both texts is the proof. |
| D33 (adopted) | **Recommended: as built.** The change is possible only while no answer runs. After the restart the same chat is opened with `session/load` (history replayed); a chat with no message gets a new chat; Always approve is off again, and a notice says so. | A restart ends a running turn. `session/load` keeps the history of the chat (D21 to D24). Always approve is never restored (D19). |
| D34 (adopted) | **Option A, chosen by the user.** The chat shows Markdown with `markdown-it` 15.0.3 and `markdown-it-cjk-friendly` 3.0.0, both bundled into `dist/webview.js`. The parser only reads. Its HTML option is off, and the webview makes DOM nodes from plain data, never from an HTML string (section 2, rule 5). Options not taken: **B**, a longer hand-written reader (no dependency, but each new mark is more code, and the rule for `**` next to Chinese, Japanese and Korean characters is hard to write); **C**, the `markdown.api.render` command of VS Code (it returns an HTML string, so the webview would need `innerHTML` and rule 5 would break). | The reader of 1.0.0 made paragraphs, lists, code and links only. In 47 session files of the author (131 answer texts, a small sample), 41 answers (31 %) had a heading, a table, bold text, a quote, a rule or a nested list, and the chat showed the marks as text. `markdown-it` is the reference parser of CommonMark and has its own test suite. Plain CommonMark does not close `**重要：**文本` (3 of 10 patterns that we tried stayed raw), and the plugin fixes it. Cost: the webview bundle grows from 11 KB to 118 KB, and `THIRD-PARTY-NOTICES` lists 8 more packages. D3 stays true: the webview is a plain TypeScript bundle with no UI framework, and the new dependency is a parser. |
