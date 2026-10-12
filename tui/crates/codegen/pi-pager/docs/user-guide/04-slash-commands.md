# Slash Commands

A slash command is a command that you type in the prompt. Type `/` on an empty prompt. A list of commands opens. Type more letters to filter the list. Press `Enter` to run the selected command.

## Built-in commands

`zypi` has nine built-in commands.

| Command | Alias | What it does |
| --- | --- | --- |
| `/new` | `/clear` | Start a new session. |
| `/resume` | | Open the list of past sessions. Select one to continue it. |
| `/quit` | `/exit` | Quit `zypi`. |
| `/help` | | Open the command palette. It lists every command and key. |
| `/theme` | `/t` | Switch the color theme. |
| `/settings` | `/config`, `/preferences`, `/prefs` | Open the settings. |
| `/multiline` | `/ml` | Swap `Enter` and `Shift+Enter`. After the swap, `Enter` inserts a new line. |
| `/model <model> [effort]` | `/m` | Switch the active model. The effort is optional. |
| `/home` | `/welcome` | Go back to the welcome screen. |

To pick a model from a list, press `F4`.

## Commands from the agent

The agent can add commands. They appear in the same list. Type `/` and read the list to see them.

## The command palette

Press `Ctrl+P`, or run `/help`. The command palette opens. It lists every command and every key binding. Type to search. Press `Enter` to run an item.

## Other places to look

- `F2` opens the settings. `/settings` does the same.
- `F3` opens the list of sessions. `/resume` does the same.
- `F4` opens the model picker.

See [Getting Started](01-getting-started.md) for the keys to learn first.
