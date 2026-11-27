# Changelog

## 1.12.0 (2026-11-25)

Stable release. It follows 1.10.0 and bundles the same agent (1.1.0). A user of 1.10.0 gets it as an update.

### New

- **Avatars.** Each message in the chat has an avatar. The `zycode` side shows a mascot that matches the model of the chat (Claude, Gemini, DeepSeek, OpenAI/GPT, Grok, Kimi or Qwen), and a mascot of your choice for another model. Your side shows a plain icon. A click on an avatar opens a menu. There you can choose a built-in mascot, choose an image file from your disk, switch the matching by model on or off, or go back to the default. The Command Palette has the same actions (**Choose user avatar**, **Choose zycode avatar**, **Choose built-in zycode avatar** and two **Reset … avatar** commands). A file that you choose is copied to the storage folder of the extension.
- **Account picture (opt-in).** The command **Use account picture as avatar** shows the picture of your GitHub or Microsoft account as your avatar. It is off until you run it. VS Code then asks you to sign in, or to allow zycode to use the account. If you say no, nothing changes. After that, zycode gets the picture in the background and sends your sign-in token to `api.github.com` or `graph.microsoft.com` only. A value that you write by hand in `settings.json` (`zycode.accountAvatar`) sends nothing when VS Code has no permission for the account. An image of your own (`zycode.userAvatar`) wins over the picture.
- **Settings button.** The gear button in the title of the chat opens the settings of zycode. **Alt+click** (Option+click on macOS) opens `agent.toml`, as the button did in 1.10.0. **Open agent.toml** is also in the `…` menu of the title.
- New settings: `zycode.autoModelAvatar`, `zycode.assistantAvatarPreset`, `zycode.assistantAvatar`, `zycode.userAvatar` and `zycode.accountAvatar`.

### Changed

- A thinking block is open while the model thinks, and it closes when the model is done. It shows how long the model thought.
- The Auto mode notice is folded in the chat by default.
- `zycode.userAvatar`, `zycode.assistantAvatar` and `zycode.accountAvatar` are per machine. VS Code does not sync them and a folder cannot set them, because a file path is valid on one machine only.
- The avatar buttons are not in the Tab order. In a long chat, Tab goes to the message box and does not stop on every avatar.
- The README, the description of the extension and the notices say that zycode is not affiliated with any maker of an AI model. The mascots are not logos. They are community illustrations.

## 1.10.0 (2026-11-25)

Stable release. It follows 1.8.0 and bundles agent 1.1.0 (1.8.0 had 1.0.1). A user of 1.8.0 gets it as an update.

### New

- **The Thinking menu changes the level live.** The bundled agent now has the `thinking_level` option, so a change takes effect at once: no question, no edit of `agent.toml`, no restart, and the chat stays open. The agent saves the level in the chat. A past chat that you open starts at its own level, and a new chat starts at the level of `agent.toml`. With `reasoning = false` the menu stays off. For an agent that you choose with `zycode.agentPath` and that has no such option (1.0.1 or older), the menu still edits one line of `agent.toml` and restarts the agent.
- **Code mode.** Agent 1.1.0 has a `codemode` tool: the model writes one Python script that calls the other tools, and the script runs in a sandbox. It is on by default. In **Ask** and **Auto**, the question card of `codemode` shows the whole script (see "Fixed"). Each tool call that the script makes is asked about on its own card, as an ordinary call. The command `/codemode [status|on|hybrid|only|off]` shows or changes the mode, and appears in the `/` menu by itself. A script has weaker limits on macOS: use a container or a virtual machine for code that you do not trust.
- **`skill-creator` skill.** Agent 1.1.0 has a built-in skill that helps the model install, check and write Agent Skills. It needs no setup.
- **Images in the chat.** Paste or drop up to 4 images into the message box (PNG, JPEG, GIF or WebP). They go to the agent only when it says that it accepts images; if not, the chat says so and sends nothing.
- **MCP servers from VS Code.** The new setting `zycode.mcpServers` lists MCP servers (stdio, and HTTP or SSE when the agent supports them). The extension gives them to the agent when a chat starts. Only your user settings count, and only in a trusted workspace. Values of `env` and `headers` are plain text in `settings.json`.
- **Add to chat.** **Add selection to chat** (editor menu) and **Add to chat context** (file menu in the Explorer) put a reference in the message box. They add to the open chat. If the file is outside the folder of that chat, a notice says so.
- **Buttons in the chat title:** **New chat**, **Restart agent** and **Open agent.toml**.
- **Prompt history.** **Up** and **Down** in the message box bring back your earlier messages, with their `@` references. **Esc** leaves the history, and **Esc** also stops a running answer.
- **Colours in code blocks.** The code blocks in answers have syntax colours.
- A **Save all** button on the notice that says that files have unsaved changes (the agent reads the files on disk).
- A file that may hold secrets (for example `.env`, `.aws/credentials` or a key file) shows a **may hold secrets** badge in the `@` list.
- A one-time tip tells you to set `context_window` in `agent.toml` to show the Context row.

### Changed

- **Bundles `zycode-agent` 1.1.0** for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64`, and `win32-x64` (was 1.0.1). The package of `linux-x64` is 73.7 MiB. Besides the items above, agent 1.1.0 shows an `edit` that you open again from a past chat as plain text, with no diff (the diff of a new call is not changed), and its `bash` tool fails a call that went over the output limit also when the command ended first. The full list of agent changes is in the [CHANGELOG of the repository](https://github.com/zy1233/zycode/blob/main/CHANGELOG.md).
- In the `@` list, the header says **References** (was "Context"). The README says that a reference sends the path only.
- When you stop the agent on Windows, the extension ends the whole process tree.
- With more than one folder open, the agent now starts in the folder of the file in the active editor (a file on disk that is inside one of the open folders). With no such editor, it starts in the first open folder, as before. The folder is chosen each time the agent starts: when the chat view first opens, at **Restart agent**, and after a trust or folder change that lets it start; a new chat keeps the folder of the running agent.
- The `@` list keeps the old rows (in italic, with "searching…") while a new search runs, and it ignores Enter, Tab and a click on them. The text box is a plain text box for a screen reader, the list names only its options, and a live region says how many suggestions there are.
- The `@` list also opens for the full-width character `＠`, and the `/` menu for `／` at the start of a line, as an input method types them.

### Fixed

- **The question card of `codemode` and of `workflow` showed no script.** The card now shows what the agent runs: the whole `code` of a `codemode` call, and for a `workflow` call the whole `script`, or, when the call has a `name`, the built-in workflow with its whole `args` as JSON (the agent ignores `script` when `name` is set, and the card says so). The whole command of `bash` is shown the same way. A long script does not get cut: the text scrolls inside the card, and **Allow once** and **Reject** stay below it. The dialog that shows when the chat view is hidden has the whole script too.
- A `.gitignore` or `files.exclude` pattern with many `*` could freeze the `@` file list for seconds, or it was dropped with no message, and a `!` rule that was dropped could leave a file hidden. Such a pattern now works and is fast. A filter uses at most 100 such patterns, and the log has one warning when more are dropped.
- Images: the state that VS Code saves for the chat view holds at most 2,000,000 characters of image data, and a notice says when an image is not saved. The message still sends every image.
- **Down** in the prompt history no longer drops an entry that you edited.
- Picking a file name in the `@` list now replaces the rest of a Chinese or Japanese file name that is after the cursor.
- A quote in a path (`@"a\"b c.txt"`) works.
- `/new` from the **/** button keeps your draft and references. A message that starts with `/` is sent as a command, and a client command also works while references are attached.
- Two messages that the agent sends outside a turn no longer join into one line.

## 1.8.0 (2026-11-20)

Stable release. It follows 1.6.0 and bundles agent 1.0.1 (1.6.0 had 1.0.0). A user of 1.6.0 gets it as an update.

### New

- **`@` context mentions in the chat composer.** Type `@` (or select the **@** button) to reference workspace files, folders, open tabs, the active file, or the active editor selection (`@path:L10-L20`). Supports smart-case fuzzy search, `Tab` drill-down into folders, attachment chips above the input (automatically pruned when you delete the inline `@` token), and workspace indexing that honors `.gitignore`, `files.exclude`, and `search.exclude` (including brace patterns).
- **`/` slash command completion.** Type `/` (or select the **/** button) to pick from agent-advertised commands (via standard ACP `available_commands_update`) and client commands (`/new`, `/mode`, `/thinking`, `/model`, `/restart`, `/setup`, `/config`, `/log`, `/guide`), with option completion for `/mode`, `/thinking`, and `/model`. Local commands (`/log`, `/config`, `/guide`, `/restart`) also work while a turn is running.
- **Model menu in the chat header.** When multiple models are configured in `agent.toml`, a **Model** selector appears in the header so you can switch models in place (also via `/model`). Switching to a model without `context_window` clears the previous model's **Context** row.
- **Hidden-view permission fallback and status bar alert.** When a permission or project-trust request arrives while the chat view is hidden, VS Code shows a warning dialog with the full command or path, exact agent options, and an **Open chat** action, while the status bar highlights `zycode: approval needed`. Dismissing the dialog leaves the request pending rather than auto-answering it.

### Changed

- **Chat state survives sidebar switches.** The chat view retains its context while hidden and persists unsent drafts, `@` attachments, scroll position, and expanded/collapsed thinking and tool sections via VS Code webview state, avoiding Markdown re-parses when switching between sidebar views.
- **Incremental, throttled tool output and thinking rendering.** Streaming `bash` output now uses incremental delta updates and 80 ms throttled in-place DOM updates instead of rebuilding the tool card on every chunk, preserving text selections and manual expand/collapse state.
- **Bundles `zycode-agent` 1.0.1** for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64`, and `win32-x64` (was 1.0.0). Agent 1.0.1 adds `thinking_format = "gemini"` and `<thought>` / OpenRouter `reasoning_details` stream extraction for `provider = "openai"` (verified with local HTTP/mock-transport tests; not verified against live Google Gemini or Vertex AI APIs) and `--version`. The full list of agent changes is in the [CHANGELOG of the repository](https://github.com/zy1233/zycode/blob/main/CHANGELOG.md).

## 1.6.0 (2026-11-19)

Stable release. It follows 1.4.0 and has the same bundled agent (1.0.0). A user of 1.4.0 gets it as an update. The agent was not changed.

### New

- **Copy buttons in the chat.** A code block has a header with its language and a **Copy** button for the whole block. Each line of a code block has its own copy button, which shows when the pointer is on the line or the button has focus. For a shell block (`sh`, `bash`, `zsh`, `powershell`, `cmd` and the like) and for a block with no language, the line copy takes off a leading `$ ` or `PS> `, so you can paste the command into a terminal. The whole-block copy keeps the text as it is. A message has a **Copy message** button. A tool card has a button that copies its command (or its path, or its title when there is neither), and the output of a command has a **Copy output** button. Click an inline `code` span, or the summary line of a question card, to copy it (a click that ends a text selection does not copy). A link is not changed: it still opens through VS Code. When the clipboard is not available, the button shows **Copy failed**. A screen reader hears "Copied" or "Copy failed".
- A **↓ Latest** button shows above the message box when you have scrolled up from the end of the chat. It goes to the newest message. Sending a message also goes to the end.

### Changed

- The chat has a new look: message headers, a card for your messages, tables and quotes with borders and colours that follow the VS Code theme, tool cards with a colour stripe for the state of the call (done, running, failed), and a thinking block that you open and close with an arrow (`…` shows while the model only thinks). Text in a code block, a link and the other old behaviour are unchanged.
- The message box grows with its text, up to 35 % of the height of the view. This needs a VS Code that has Chromium 123 or later. An older VS Code (1.90 and later still work) keeps the fixed height of three lines.
- **Enter** no longer sends a message while an input method (IME) is composing text, also in a browser engine that reports only `keyCode` 229 for the composing key.

## 1.4.0 (2026-11-16)

Stable release. It follows 1.2.0 and bundles agent 1.0.0 (1.2.0 had 0.9.1). A user of 1.2.0 gets it as an update.

### New

- The **Thinking** menu has a new level, `max`, above `xhigh`. The menu offers `max` only for an agent of version 1.0.0 or later, because an older agent reads `max` as `off` and says nothing. The bundled agent is 1.0.0, so its menu has `max`. An older agent that you choose with `zycode.agentPath` keeps the six levels. The extension does not know which model takes `max`. A model that does not take it answers with an error. Then choose a lower level.
- The **Context** row now shows with the bundled agent. Agent 1.0.0 sends the standard ACP message `usage_update` when the model has `context_window` in `agent.toml` (a whole number of tokens). The row has a bar, the percent and the tokens, and the VS Code status bar shows `· context 42%`. A model with no `context_window` gets no row. The agent sends no cost, so there is no **Cost** line (the agent has no price table). Part of the count is an estimate: the tool results of a turn, and the whole count after a compaction. The next model call gives an exact number.

### Changed

- Bundles `zycode-agent` 1.0.0 for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` and `win32-x64`. Version 1.2.0 had 0.9.1. Two more changes of the agent reach you with it. When the model has a `context_window`, the agent compacts a long chat by itself (`auto_compact = false` at the top of `agent.toml` turns this off). When an answer ends because the agent reached `max_turns`, the stop reason is `max_turn_requests` (it was `end_turn`), and the chat shows "The turn ended: max_turn_requests." The list of all changes of agent 1.0.0 is in the [CHANGELOG of the repository](https://github.com/zy1233/zycode/blob/main/CHANGELOG.md).

## 1.2.0 (2026-11-13)

Stable release. It follows 1.0.0 and has the same bundled agent (0.9.1). A user of 1.0.0 gets it as an update.

### New

- The chat shows Markdown as Markdown. Before, only paragraphs, lists, code and links worked, and a heading, a table or bold text showed its marks (`##`, `|---|`, `**`). Now the chat shows headings, bold, italic, strikethrough, nested lists, quotes, rules and tables (with column alignment, and a scroll bar for a wide table). Bold text next to Chinese, Japanese or Korean characters works (`**重要：**文本`). Raw HTML in an answer stays text, an image is not loaded (it shows as a link), and a link with an address that the extension does not open (for example `javascript:`) shows as plain text with its address. Bold, italic, strikethrough and links nest at most 32 levels deep, and a deeper one shows only its text.

- The status row of the chat is richer. It shows the time of the turn (a clock while the agent works, then `last turn 8.4 s`) and the folder of the chat. When the agent sends the standard ACP message `usage_update`, a **Context** row shows a bar, the percent, the tokens and the cost, and the VS Code status bar shows `· context 42%`. The bar turns to a warning colour from 80 % and to a danger colour from 90 %. **Agent 0.9.1, which is bundled, does not send `usage_update`, so the Context row does not show with it yet.** The extension does not count tokens and does not guess the size of the window. The agent was not changed: [an investigation record](https://github.com/zy1233/zycode/blob/main/docs/specs/2026-11-13-agent-context-usage.md) says what a change of the agent needs.

### Changed

- The chat draws a streamed answer at most once each 80 ms (the first piece at once, the last piece always). A long answer then needs less work while it streams in.
- `THIRD-PARTY-NOTICES` lists the packages of the Markdown parser. It also lists a bundled package that sits inside another one (`entities`, inside `markdown-it`).

## 1.0.0

First stable release. The pre-release 0.1.0 is replaced by this version. The version is above 0.1.0, so a user of the pre-release gets it as an update.

### New

- Getting-started guide (a VS Code walkthrough) for the first run: open a folder, choose a model and save a key, say hello, choose a mode, find past chats, and what to do when something fails. It opens once by itself when nothing is set up. Open it again at any time: **zycode: Open the getting-started guide**.
- First-run help in the chat: the banner shows the steps and a **Run setup** button, and a key that the provider refuses, a wrong model id or a server that cannot be reached gets a short explanation with buttons (**Change the key**, **Open agent.toml**, **Show log**). **zycode: Set up model and API key** also changes the key later.
- **Always approve** in the mode menu, next to **Ask** and **Auto**. A modal warning comes first: every tool call, `bash` included, then runs with no question. A label shows while it is on. A new chat, a restart and a past chat that is opened always start in Ask (or in Auto, if the setting says so). It is not offered in a folder that you did not trust, and not after you refused the project-trust question of the agent.
- **Thinking** menu in the chat header: the thinking level of the model (`xhigh` to `off`). Agent 0.9.1 reads the level from `agent.toml` at its start and cannot change it while it runs, so a change asks first, edits the one line `thinking_level` in `agent.toml`, restarts the agent and opens the same chat again. The level is the same for every chat. The menu is off, with a reason, for a model with `reasoning = false`.
- **Past chats** view under the chat: the chats of the open folder, newest first, with the first message and the time of the last one. Select a chat to open it with its history. The trash button deletes a chat, after a question. The list also has an empty state and an error state.

### Changed

- The setting `zycode.permissionMode` still offers `ask` and `auto` only. **Always approve** is chosen in the chat and is never restored.
- The extension is published as a stable release (no pre-release flag in the package or in the publish workflow).
- Bundles `zycode-agent` 0.9.1 for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` and `win32-x64`. Version 0.1.0 had 0.9.0.
- Agent 0.9.1 shows the thinking that a gateway streams as `reasoning_content` when `provider = "openai"` (the agent fix #121).

## 0.1.0 (pre-release)

First version. Published as a pre-release (`--pre-release`).

- Chat view in the activity bar, with the status of the agent, its version, the model and the permission mode.
- Permission questions for every tool call that is not read-only, and the project-trust question of the agent.
- **Show diff** for `write` and `edit` calls, before and after the call. The extension never writes your files.
- First-run setup in a VS Code terminal (`--setup`); no key goes through the chat.
- Setting `zycode.permissionMode` (`ask` or `auto`). The extension sets the mode of each session. This version never set `always-approve` (1.0.0 sets it from the mode menu, after a warning).
- No telemetry.
- Bundles `zycode-agent` 0.9.0 for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` and `win32-x64`. The binaries are not code-signed. Only the OpenAI-format provider is in them.
