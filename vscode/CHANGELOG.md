# Changelog

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

- Bundles `zycode-agent` 1.0.0 for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` and `win32-x64`. Version 1.2.0 had 0.9.1. Two more changes of the agent reach you with it. When the model has a `context_window`, the agent compacts a long chat by itself (`auto_compact = false` at the top of `agent.toml` turns this off). When an answer ends because the agent reached `max_turns`, the stop reason is `max_turn_requests` (it was `end_turn`), and the chat shows "The turn ended: max_turn_requests." The list of all changes of agent 1.0.0 is in the [CHANGELOG of the repository](https://github.com/zy1233/pi-python/blob/main/CHANGELOG.md).

## 1.2.0 (2026-11-13)

Stable release. It follows 1.0.0 and has the same bundled agent (0.9.1). A user of 1.0.0 gets it as an update.

### New

- The chat shows Markdown as Markdown. Before, only paragraphs, lists, code and links worked, and a heading, a table or bold text showed its marks (`##`, `|---|`, `**`). Now the chat shows headings, bold, italic, strikethrough, nested lists, quotes, rules and tables (with column alignment, and a scroll bar for a wide table). Bold text next to Chinese, Japanese or Korean characters works (`**重要：**文本`). Raw HTML in an answer stays text, an image is not loaded (it shows as a link), and a link with an address that the extension does not open (for example `javascript:`) shows as plain text with its address. Bold, italic, strikethrough and links nest at most 32 levels deep, and a deeper one shows only its text.

- The status row of the chat is richer. It shows the time of the turn (a clock while the agent works, then `last turn 8.4 s`) and the folder of the chat. When the agent sends the standard ACP message `usage_update`, a **Context** row shows a bar, the percent, the tokens and the cost, and the VS Code status bar shows `· context 42%`. The bar turns to a warning colour from 80 % and to a danger colour from 90 %. **Agent 0.9.1, which is bundled, does not send `usage_update`, so the Context row does not show with it yet.** The extension does not count tokens and does not guess the size of the window. The agent was not changed: [an investigation record](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-11-13-agent-context-usage.md) says what a change of the agent needs.

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
