# Getting Started

`zypi` is a terminal interface for a coding agent. You type a request. The agent reads your files, runs commands, and edits code. `zypi` shows the work as it happens.

`zypi` does not contain the agent. `zypi` starts the Python agent (`pi_agent_cli`) as a child process. The two programs talk through the Agent Client Protocol (ACP). The agent holds the model settings and runs the tools.

## Before you start

You need:

- Python 3.11 or later, with the agent installed: `pip install pi-agent-cli-lc`.
- A model and an API key for it. See [Set up the model](#set-up-the-model).

## Set up the model

The agent reads `agent.toml` in the `zypi` home directory. The home directory is `~/.pi-python`. To use a different directory, set the `PI_HOME` environment variable.

This is a minimum `agent.toml`:

```toml
[model]
provider = "openai"
id = "gpt-4.1"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

`api_key_env` is the name of an environment variable. Do not write the key in `agent.toml`. To keep the key out of your shell profile, write `KEY=VALUE` lines in `~/.pi-python/local.env`. The agent loads them when it starts. It does not replace a variable that is already set.

The file `agent.example.toml` in the `pi-agent-cli` package lists every key.

## Start `zypi`

Open a terminal in your project directory. Run:

```
zypi
```

To start with a prompt, give the prompt as an argument:

```
zypi "explain the layout of this project"
```

To run one turn without the screen, use `-p`. `zypi` prints the answer to stdout and exits:

```
zypi -p "list the TODO comments in src"
```

If `zypi` cannot start the agent, it prints the name of the program and the end of the agent log. See [Where the agent comes from](#where-the-agent-comes-from).

## Send the first prompt

Type your request in the prompt at the bottom of the screen. Press `Enter` to send it. The answer streams into the scrollback above the prompt. The scrollback holds the replies, the commands, and the file edits.

The agent asks before it runs a tool that can change something. Read the request, then allow it or reject it. See [Permissions and Safety](22-permissions-and-safety.md).

## Keys to know

| Keys | Action |
| --- | --- |
| `Enter` | Send the prompt. |
| `Esc` | Stop the running turn. |
| `Ctrl+C` | Stop the running turn when the prompt is empty. When the prompt has text, clear the text. |
| `Tab` | Move the focus between the prompt and the scrollback. |
| `Shift+Tab` | Switch between Normal and Always-approve. |
| `Ctrl+P` | Open the command palette. |
| `F2` | Open the settings. |
| `F3` | Open the list of sessions. |
| `F4` | Pick a model. |
| `Ctrl+N` | Start a new session. Press the keys twice. |
| `Ctrl+Q` | Quit. Press the keys twice. In a VS Code terminal, use `Ctrl+D`. |

The command palette lists every key and every command. Press `Ctrl+P` when you do not remember a key.

## Where the agent comes from

`zypi` looks for the agent command in this order:

1. The `PI_AGENT_COMMAND` environment variable.
2. `[agent].command` in `agent.toml`.
3. The `PI_PYTHON` environment variable. `zypi` adds `-m pi_agent_cli` to it.
4. `python -m pi_agent_cli` (`python3` on Linux and macOS).

The agent writes its error output to `~/.pi-python/logs/agent.stderr.log`. Read this file when the agent stops or does not answer.

## Next

- [Slash Commands](04-slash-commands.md)
- [Sessions](17-sessions.md)
- [Permissions and Safety](22-permissions-and-safety.md)
- [Theming and Appearance](06-theming.md)
- [Terminal Support and Troubleshooting](21-terminal-support.md)
