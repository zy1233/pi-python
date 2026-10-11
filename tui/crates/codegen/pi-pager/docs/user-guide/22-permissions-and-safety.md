# Permissions and Safety

The agent can run commands and change files on your computer. This page tells you when the agent asks first, how to change that, and what the agent sends to the model provider.

## Permission modes

`zypi` has two permission modes.

| Mode | What happens |
| --- | --- |
| Normal | The agent asks before a tool call, unless the tool is harmless (see below). |
| Always-approve | The agent runs every tool call without asking. |

Normal is the default. To change the mode:

- Press `Shift+Tab` to switch between Normal and Always-approve.
- Press `Ctrl+O` to switch Always-approve on or off directly.
- Start `zypi` with `--always-approve`, or with `--permission-mode bypassPermissions`, for Always-approve from the start.
- Start `zypi` with `--permission-mode default` for Normal. `zypi` rejects every other value of `--permission-mode`, because the agent does not act on them.

`agent.toml` has a third value: `permission = "auto"`. It behaves like `always-approve`. The agent never asks.

## When the agent asks

In Normal mode, the agent asks before a tool call. You can allow the call once, or reject it. If you reject a call, the tool does not run.

The agent does not ask for a tool that declares itself harmless. These tools are harmless:

- `read`, `grep`, `find`, and `ls`.
- `web_search` and `fetch_url`.
- `goal_update` and `goal_complete`. They only keep notes in the session.

The agent always asks for `bash`, `edit`, `write`, and `workflow`. The agent also asks for a tool that declares nothing. Extension tools are in this group unless the extension says that a tool is harmless.

In headless mode (`zypi -p`), the agent runs one turn without a screen. It cannot ask, so it runs every tool call. Use `-p` only in a directory where this is safe.

## Project trust

A project can carry files that change what the agent does. The agent uses these files only after you trust the project:

- Extensions in `<project>/.pi-python/extensions`. The agent runs their Python code when a session opens.
- Saved workflows in `<project>/.pi-python/workflows`. Each workflow becomes a slash command.
- The prompt files `<project>/.pi/SYSTEM.md` and `<project>/.pi/APPEND_SYSTEM.md`.
- Skills in a project path, for example `.pi/skills`.

If a project has such files, the agent asks you once for each directory. The choices are **Don't trust** and **Trust and remember**. A saved answer is in `~/.pi-python/agent/trust.json`. The agent asks again when one of the files changes. To take the trust back, delete the entry of the project from `trust.json`.

You can also set the trust in the `agent.toml` file of your home directory:

```toml
[extensions]
default_project_trust = "ask"        # "ask", "never", or "always"
trusted_projects = ["~/work/my-repo"] # the agent does not ask about these
```

A file inside the project cannot set the trust. This stops a repository from trusting itself.

The agent always reads `AGENTS.md` and `CLAUDE.md` in your project. These files do not need trust.

## What the agent sends to the model provider

- Your prompts, the replies, and the output of the tools that the agent runs. This is how the agent works.
- A snapshot of `git status`: the branch name and the paths of changed and untracked files. The agent sends it with every request. To stop this, set `enabled = false` under `[git]` in `agent.toml`.

## Keys and secrets

Keep API keys in environment variables. `agent.toml` has only the name of the variable (`api_key_env`). To avoid a shell profile, write `KEY=VALUE` lines in `~/.pi-python/local.env`.

The agent writes error output to `~/.pi-python/logs/agent.stderr.log`. A traceback in this file can quote prompts, paths, and keys. Do not share the file without a check.
