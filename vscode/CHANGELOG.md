# Changelog

## Unreleased

### New

- The chat shows Markdown as Markdown. Before, only paragraphs, lists, code and links worked, and a heading, a table or bold text showed its marks (`##`, `|---|`, `**`). Now the chat shows headings, bold, italic, strikethrough, nested lists, quotes, rules and tables (with column alignment, and a scroll bar for a wide table). Bold text next to Chinese, Japanese or Korean characters works (`**重要：**文本`). Raw HTML in an answer stays text, an image is not loaded (it shows as a link), and a link with an address that the extension does not open (for example `javascript:`) shows as plain text with its address.

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
