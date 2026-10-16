# Sessions

A session is one conversation with the agent. The agent saves each session in a file. You can continue a session later.

## Where sessions are stored

The agent writes one JSON Lines file for each session. The files are in the `sessions` directory of the `zypi` home directory (`~/.pi-python/sessions`). Each file name starts with the creation time. Each file name ends with the session id.

To see how much disk space the home directory uses, run `zypi du`.

## Start a session and continue a session

| Task | How |
| --- | --- |
| Start a new session | Run `/new` (alias `/clear`), or press `Ctrl+N` twice. |
| Open the list of past sessions | Run `/resume`, or press `F3`. |
| Continue the latest session of the current directory | Run `zypi -c` or `zypi --resume`. |
| Continue a session by id or by title | Run `zypi --resume <id-or-title>`. |
| Start a new session with an id that you choose | Run `zypi --session-id <uuid>`. |

The list of sessions shows the newest session first. The title of a session is its first message.

When `zypi` starts, it asks the agent for the list of sessions. It does not read the session files. If the session that you name does not exist, `zypi` prints a message and stops with exit status 1. The terminal UI does not start.

The latest session is the session with the latest activity. This is the session that the agent wrote to last. It is not always the session that you created last.

An id that you give to `--resume` is found in every directory. The agent loads the session in the directory where the session started.

A title that you give to `--resume` matches the titles of the sessions of the current directory. The letter case does not matter. Spaces at the two ends of the title do not matter. If two sessions have the same title, `zypi` stops with an error. The error lists the ids and the time of the last activity of these sessions. Use `--resume <id>` then. A value in the form of a UUID is always an id.

A session id that you choose with `--session-id` must be a UUID. No session in any directory can have this id. To check this, `zypi` reads the whole list of sessions. If a session has the id, `zypi` prints `Session ID <id> is already in use.` and stops with exit status 1. Do not use `--session-id` together with `--resume` or `-c`.

The agent writes the file of a session when the session starts. A session in which you typed nothing has the title `(no messages)`. `zypi -c` takes such a session when it is the latest one.

## What a session keeps

A session keeps the full history of the conversation. It also keeps the model that you picked with `F4` or `/model`. When you continue the session, the agent uses that model again. The agent restores the model only if the model is still in `agent.toml`.

## The directory of a session

The agent works in the directory that it saved with the session. `zypi` does not read this directory yet. To keep the two the same, start `zypi` in the directory where the session started.

## Export a session

To write a session as Markdown, run:

```
zypi export <session-id> notes.md
```

If you omit the file name, `zypi` writes the Markdown to stdout. To copy the Markdown to the clipboard, add `-c`.

`zypi export` asks the agent for the session. It starts the agent, loads the session and stops the agent when it has the text. This takes about as long as `zypi --resume` takes to show the conversation. The Markdown has what a resumed session shows: your messages, the replies of the agent, and one line for each tool call. If the agent compacted the session, the text starts at the first message that the compaction kept.

If no session has the id, `zypi` shows an error and writes nothing.
