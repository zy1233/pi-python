# zycode for VS Code

**Unofficial; not affiliated with pi or Microsoft.**

zycode is a coding agent. This extension puts it in a chat view in VS Code. The agent can read your files, edit them, and run commands, and it asks you before it does anything that is not read-only. The extension starts the agent program on your machine and talks to it over the [Agent Client Protocol](https://agentclientprotocol.com). Source: <https://github.com/zy1233/pi-python> (`packages/pi-vscode`). Site: <https://zycode.dev>.

## What you get

- A chat view in the activity bar, with the status of the agent, its version, the model, and the permission mode.
- A question card for every tool call that is not read-only. You choose *Allow once* or *Reject*.
- A **Show diff** button for `write` and `edit` calls, before the call (what it would change) and after it (what it changed). The diff is for reading. The extension never writes your files.
- A getting-started guide for the first start (a VS Code walkthrough), and help in the chat when the agent has no model, no key, or a key that is refused. The setup runs in a VS Code terminal, and you type the key there.
- A **Past chats** view under the chat: the chats of the open folder. Open one to continue it, or delete it.
- A mode menu in the chat: **Ask**, **Auto** and **Always approve**.
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
- One chat at a time (open a past chat to switch). No model choice, no images, no `@file` mentions yet.

## Third-party software

The package holds the agent program (a PyInstaller build of the Python agent). The licenses of what is inside are in `THIRD-PARTY-NOTICES`. This extension is MIT licensed (`LICENSE`).
