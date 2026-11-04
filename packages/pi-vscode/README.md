# zycode for VS Code

**Unofficial; not affiliated with pi or Microsoft.** This is a pre-release.

zycode is a coding agent. This extension puts it in a chat view in VS Code. The agent can read your files, edit them, and run commands, and it asks you before it does anything that is not read-only. The extension starts the agent program on your machine and talks to it over the [Agent Client Protocol](https://agentclientprotocol.com). Source: <https://github.com/zy1233/pi-python> (`packages/pi-vscode`). Site: <https://zycode.dev>.

## What you get

- A chat view in the activity bar, with the status of the agent, its version, the model, and the permission mode.
- A question card for every tool call that is not read-only. You choose *Allow once* or *Reject*.
- A **Show diff** button for `write` and `edit` calls, before the call (what it would change) and after it (what it changed). The diff is for reading. The extension never writes your files.
- A setup flow for the first start: if the agent has no model and no API key, the chat shows **Run setup**. The setup runs in a VS Code terminal, and you type the key there.
- Commands (Command Palette, category *zycode*): Open chat, New chat, Stop, Restart agent, Run setup, Show log.

## The first start

1. Open a folder, then open the zycode view.
2. The first start takes a few seconds. The agent is one file that unpacks itself.
3. If the chat says *zycode needs a model and an API key*, click **Run setup** and follow the questions in the terminal. When the terminal closes with exit code 0, the extension restarts the agent.

The agent keeps its settings in `~/.pi-python` (`agent.toml` and `local.env`). The extension never sees your key: you type it in the terminal and the agent saves it.

## Permissions

- The setting `zycode.permissionMode` has two values. `ask` (default) asks about every call that is not read-only. `auto` runs the calls that are clearly inside the scope and asks about the rest.
- The extension can change the mode only when the agent offers a `mode` option. **The bundled agent 0.8.0 does not offer it.** It reads `permission` from `agent.toml` (default `ask`). If you set `always-approve` there, this agent runs every call with no question, and this extension cannot stop it. Do not set it. With an agent that has the option, the extension sets `ask` or `auto` by itself and never `always-approve`.
- **Auto is not a sandbox.** It guards what a call writes or runs. It does not guard what a call reads or sends: `read`, `grep`, `find`, `ls`, web fetch, and recursive reads in `bash` run with no question. They can show a secret to the model or send it to a server. If you need a boundary, use a container or a virtual machine.
- With the bundled agent 0.8.0, `auto` has no effect. The chat then says "This agent version has no Auto mode."

## Two kinds of trust

- **VS Code Workspace Trust** decides if zycode starts at all. In Restricted Mode the extension does nothing, because the agent runs programs on your machine.
- **The agent's project trust** is a separate question. When a project ships its own extensions, saved workflows, prompt files or skills, the agent asks if it may load them. The chat shows this as *Project trust*. It is not the Workspace Trust question that you already answered.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `zycode.agentPath` | empty | A program to run instead of the bundled agent: an absolute path, or a name that is found on `PATH`. |
| `zycode.agentArgs` | `[]` | Arguments for it. For a Python install: path `python`, arguments `["-m", "pi_agent_cli"]`. |
| `zycode.permissionMode` | `ask` | `ask` or `auto` (see above). |
| `zycode.showThinking` | `true` | Show the thinking blocks of the model (collapsed). |

`zycode.agentPath` and `zycode.agentArgs` do not read the settings of a folder (`.vscode/settings.json`): a folder must not choose the program that runs on your machine. The same is true for `zycode.permissionMode`.

## Privacy

The extension has no telemetry and sends nothing to its authors. The agent sends your conversation, and the files that it reads, to the model service that you chose in `agent.toml`. The extension and the authors see none of it.

## Limits of this pre-release

- The bundled agent is **not code-signed**. On macOS and Windows the system can warn when it starts.
- The bundled agent has only the OpenAI-format provider (and services that speak that format). For the Anthropic or DeepSeek provider, install the Python agent and set `zycode.agentPath`.
- On Linux the bundled agent needs glibc 2.35 or newer. There is no package for Alpine (musl), 32-bit ARM, or Windows on ARM.
- One chat at a time. No session list or resume, no model choice, no images, no `@file` mentions yet.

## Third-party software

The package holds the agent program (a PyInstaller build of the Python agent). The licenses of what is inside are in `THIRD-PARTY-NOTICES`. This extension is MIT licensed (`LICENSE`).
