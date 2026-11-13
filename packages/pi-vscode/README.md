# zycode for VS Code

**Unofficial; not affiliated with pi or Microsoft.**

zycode is a coding agent. This extension puts it in a chat view in VS Code. The agent can read your files, edit them, and run commands, and it asks you before it does anything that is not read-only. The extension starts the agent program on your machine and talks to it over the [Agent Client Protocol](https://agentclientprotocol.com). Source: <https://github.com/zy1233/pi-python> (`packages/pi-vscode`). Site: <https://zycode.dev>.

## What you get

- A chat view in the activity bar, with the status of the agent, its version, the model, and the permission mode.
- A status row at the top of the chat: the state of the agent with the time of the turn (a clock while it works, and how long the last turn took), the version, the model and the folder. When the agent reports how full the context window is, a **Context** row adds a bar, the percent, the tokens and the cost. The VS Code status bar shows `zycode: <state>`, and `· context 42%` when the agent reports it.
- Markdown in the answers: headings, bold, italic, strikethrough, lists, quotes, tables, code blocks and links. A link opens through VS Code, and only `http`, `https` and `mailto` links open.
- A question card for every tool call that is not read-only. You choose *Allow once* or *Reject*.
- A **Show diff** button for `write` and `edit` calls, before the call (what it would change) and after it (what it changed). The diff is for reading. The extension never writes your files.
- A getting-started guide for the first start (a VS Code walkthrough), and help in the chat when the agent has no model, no key, or a key that is refused. The setup runs in a VS Code terminal, and you type the key there.
- A **Past chats** view under the chat: the chats of the open folder. Open one to continue it, or delete it.
- A mode menu in the chat: **Ask**, **Auto** and **Always approve**.
- A **Thinking** menu next to it: the thinking level of the model (`off` to `xhigh`). A change edits one line of `agent.toml` and restarts the agent (see Thinking level).
- Commands (Command Palette, category *zycode*): Open chat, New chat, Stop, Restart agent, Set up model and API key, Open the getting-started guide, Open agent.toml, Show log, Refresh past chats.

## The first start

1. Open a folder, then open the zycode view.
2. The first start takes a few seconds. The agent is one file that unpacks itself.
3. If the agent has no model, the guide opens once by itself, and the chat shows the steps and **Run setup**. Follow the questions in the terminal. When the terminal closes with exit code 0, the extension restarts the agent. Then say hello.

Open the guide again at any time: Command Palette, **zycode: Open the getting-started guide** (or *Welcome: Open Walkthrough*, then *Get started with zycode*).

The agent keeps its settings in `~/.pi-python` (`agent.toml` and `local.env`), or in the folder that `PI_HOME` names. **zycode: Open agent.toml** opens `agent.toml`. It has the model and the *name* of the key variable, and no key. The key is in `local.env`, a file that only your user can read.

**The extension never sees your key.** You type it in the terminal, and the agent saves it in `local.env`. The extension does not read, log or store it. Never type a key in the chat or in the VS Code settings.

What the chat does when the first start does not work:

| What happens | What the chat says | What you do |
|---|---|---|
| No `agent.toml`, or no model in it | *zycode has no model yet*, with the steps | **Run setup** |
| A model is set, but its key variable has no value | The name of the variable | **Run setup** |
| The provider refuses the key | *The model provider did not accept the API key* | **Change the key** (runs the setup again) |
| The provider does not know the model id | *The provider does not know this model* | **Open agent.toml**, correct `id` or `base_url`, then **Restart** |
| The model server cannot be reached | *zycode could not reach the model server* | Check `base_url` and your network |

## Past chats

The **Past chats** view is under the chat. It lists the chats that the agent saved for the open folder: newest first, each with its first message and the time of its last message. The list comes from the agent. The extension reads no session file.

- Select a chat to open it. Its history shows in the chat, and you can continue. The chat that was open is closed (it stays in the list).
- Select the trash button of a chat to delete it. A question asks first. A deleted chat cannot come back. If the open chat is deleted, a new chat starts.
- **New chat** (the plus button of the chat) starts an empty chat. A chat appears in the list after its first message. The list updates after each answer, after a new chat, and after a delete.
- A chat of another folder never shows in the list. The extension checks the folder of each chat twice, and it refuses to open a chat that belongs to another folder. The list is not available in Restricted Mode.
- If the list cannot be read, the view says why. An older agent that has no delete shows no trash button.

## Permissions

- The mode menu in the chat has three modes. **Ask** (default) asks about every call that is not read-only. **Auto** runs the calls that are clearly inside the scope and asks about the rest. **Always approve** asks about nothing.
- The setting `zycode.permissionMode` sets the mode of a start. It has two values, `ask` and `auto`. The extension sets the mode of each session with the `mode` option of the agent. This overrides `permission` in `agent.toml`, also when `agent.toml` says `always-approve`. An agent that has no `mode` option (an older `zycode.agentPath` program) keeps the mode of `agent.toml`, and the chat says so for `auto`.
- **Always approve** is chosen in the menu of the chat only. A modal warning comes first: every tool call, `bash` included, then runs with no question. While it is on, the header and the status bar show **Always approve**. It is never restored: a new chat, a past chat that you open, and a restart of the agent or of VS Code start in Ask (or in Auto, if the setting says so). It is not offered in a folder that you did not trust (Restricted Mode), and it is taken away if you refuse the project-trust question of the agent. An agent that is older than 0.9.0 has no mode menu, so there is no **Always approve** for it. The extension uses the agent's own signal for this (`pi/yolo_mode_changed`), and it adds no wire item of its own.
- **Auto is not a sandbox.** It guards what a call writes or runs. It does not guard what a call reads or sends: `read`, `grep`, `find`, `ls`, web fetch, and recursive reads in `bash` run with no question. They can show a secret to the model or send it to a server. If you need a boundary, use a container or a virtual machine.
- **Auto sends data to a model.** For a `bash` command that no rule decides, Auto sends your messages and that command to the model service of the session (or to the model named in `[auto] model` of `agent.toml`). `[auto] classifier = "rules"` in `agent.toml` turns this off. In `ask` mode this does not happen.

## Two kinds of trust

- **VS Code Workspace Trust** decides if zycode starts at all. In Restricted Mode the extension does nothing, because the agent runs programs on your machine.
- **The agent's project trust** is a separate question. When a project ships its own extensions, saved workflows, prompt files or skills, the agent asks if it may load them. The chat shows this as *Project trust*. It is not the Workspace Trust question that you already answered.

## Thinking level

The **Thinking** menu in the chat header shows the thinking level that the running agent uses, and it lists the six levels that the agent knows: `xhigh`, `high`, `medium`, `low`, `minimal` and `off` (the order of the `zypi` menu).

- **The agent cannot change the level while it runs** (agent 0.9.1). It reads `thinking_level` from `agent.toml` when it starts, and it has no setting, option or command for it over ACP. So the menu changes the file, not the agent: when you pick a level, a modal question names the file and the line, and after **Set and restart** the extension writes `thinking_level = "<level>"` in `agent.toml`, restarts the agent, and opens the same chat again with its history. Nothing else in the file changes: the extension edits one line (or adds it above the first table), keeps your comments and line ends, checks the result by reading both texts as TOML, and writes through a temporary file. If the line is written in a form that it does not edit, it changes nothing and tells you to edit it by hand.
- **The level is one setting for all chats.** It is in `agent.toml`, so a new chat, a past chat that you open, a restart of the agent and a restart of VS Code all start at the level that the file has, and `zypi` in a terminal uses the same level. A chat does not keep a level of its own: the agent does not save it in the chat. There is no `zycode` setting for it, because it could differ from the file.
- **A model that does not think.** If `agent.toml` says `reasoning = false` for the model, the agent asks no provider to think, whatever the level is. The menu is then off, and its tooltip says why. The agent does not tell a client which model can think: the file is the only place that says it. If `reasoning` is not set, the menu is on, and a level other than `off` makes the agent treat the model as one that thinks. A model or provider that does not know thinking can answer with an error. Then set the level to `off`.
- The menu is off while an answer runs, when `agent.toml` does not exist yet (run the setup first) and when it is not valid TOML.
- The menu does not change how thinking blocks are shown. That is `zycode.showThinking`.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `zycode.agentPath` | empty | A program to run instead of the bundled agent: an absolute path, or a name that is found on `PATH`. |
| `zycode.agentArgs` | `[]` | Arguments for it. For a Python install: path `python`, arguments `["-m", "pi_agent_cli"]`. |
| `zycode.permissionMode` | `ask` | The mode of a start: `ask` or `auto` (see above). It cannot be `always-approve`. |
| `zycode.showThinking` | `true` | Show the thinking blocks of the model (collapsed). |

`zycode.agentPath` and `zycode.agentArgs` do not read the settings of a folder (`.vscode/settings.json`): a folder must not choose the program that runs on your machine. The same is true for `zycode.permissionMode`.

## Privacy

The extension has no telemetry and sends nothing to its authors. The agent sends your conversation, and the files that it reads, to the model service that you chose in `agent.toml`. In `auto` mode it also sends some `bash` commands to a model (see Permissions). The extension and the authors see none of it.

## Limits

- The bundled agent is **not code-signed**. On macOS and Windows the system can warn when it starts.
- The bundled agent has only the OpenAI-format provider (and services that speak that format). For the Anthropic or DeepSeek provider, install the Python agent and set `zycode.agentPath`.
- On Linux the bundled agent needs glibc 2.35 or newer. There is no package for Alpine (musl), 32-bit ARM, or Windows on ARM.
- The thinking level is a line of `agent.toml`, and a change restarts the agent (the chat is opened again; a running answer must end first; Always approve is off again after the restart). A live setting needs a change in the agent.
- The **Context** row shows only when the agent sends the standard ACP message `usage_update`. Agent 0.9.1, which is bundled, does not send it, so the row does not show yet. The extension does not count tokens and does not guess the size of the window. What an agent change needs is in [the investigation record](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-11-13-agent-context-usage.md).
- One chat at a time (open a past chat to switch). No model choice, no images, no `@file` mentions yet.
- Markdown in an answer: raw HTML shows as text, and an image is not loaded (it shows as a link). There is no math, no footnote and no task-list box: their marks show as text (but the line of a footnote definition, `[^1]: …`, does not show, because Markdown reads it as the definition of a link). A link with an address that the extension does not open (for example `javascript:`) shows its text and its address as plain text.

## Third-party software

The package holds the agent program (a PyInstaller build of the Python agent). The licenses of what is inside are in `THIRD-PARTY-NOTICES`. This extension is MIT licensed (`LICENSE`).
