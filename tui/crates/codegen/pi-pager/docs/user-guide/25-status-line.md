# Status Line

> **Status.** The row works with the Python agent, with limits. The Python agent sends no status update, so `zypi` builds the row from what it knows itself: the directory, the session id, the model, the session name, how long it has held the session, and when the turn began. The agent does not tell `zypi` the size of the context window, the tokens in use, or a cost. The `context` and `cost` items therefore stay empty, and a script does not get those fields. An agent that does send a status update is read first (see [Available data](#available-data)).

An optional row at the bottom of the pager — above the shortcuts bar in the full screen, under the prompt's info row in minimal mode — and disabled by default. It shows live session context, such as the model and directory (and, from an agent that reports them, context-window usage, cost, and git worktree), or the output of any script you configure. Opt in with `[ui.status_line]` in `~/.pi-python/config.toml`.

## Set up

### Built-in

```toml
[ui.status_line]
type = "builtin"
items = ["cwd", "model", "context"]   # default when omitted
```

This renders, for example, `my-project │ my-model │ 12% ctx` with an agent that reports the context, and `my-project │ my-model` with the Python agent, which does not. Items appear in the order you list them, and an item with no data is left out. Long ones are elided with `…`: the directory and session name at 40 columns, the model at 30.

| Item | Shows |
| --- | --- |
| `cwd` | Current directory (basename) |
| `model` | Model display name |
| `context` | Context-window percent, amber at the auto-compaction threshold or at 80% when the agent reports none. Needs a status update from the agent, so the Python agent never shows it |
| `cost` | Session cost, hidden below $0.005 so it never shows a misleading `$0.00`. Needs a status update from the agent, so the Python agent never shows it |
| `turn-timer` | Elapsed time of the running turn, from one second in |
| `session-name` | Session name, when set |

### Command

Point `command` at a script. `zypi` pipes [JSON](#available-data) to it on stdin and shows its stdout. A `~/` prefix expands to your home directory.

```toml
[ui.status_line]
type = "command"
command = "~/.pi-python/statusline.sh"
```

Field names and nesting follow the common status line convention, so a ported script usually needs a small edit rather than a rewrite. Anything the table below does not list is not sent.

### Disabled

`type = "disabled"`, the default, shows nothing; `off`, `none`, and `hidden` are accepted as spellings of `disabled`.

### Options

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `type` | string | `disabled` | `builtin`, `command`, or `disabled`. |
| `items` | array | `["cwd", "model", "context"]` | Built-in segments, in order. |
| `command` | string | none | Script for `type = "command"`. |
| `padding` | integer | `0` | Horizontal spacing, in characters per side, capped at 16. A padding wide enough to leave no columns reserves the row but paints nothing in it. |
| `refresh_interval` | integer | unset | `command` rows only, in seconds, 1 to 86,400. Re-runs the script this often even when nothing changed, so an idle session can still surface a change — an incident page, a CI status. Unset keeps the row event-driven. The run it schedules carries `"trigger": "refresh_interval"`, and its failures keep the last output rather than painting an error (see [Refresh runs](#refresh-runs)). A script that calls a network should prefer a longer interval and read a cache on `state` runs. |

## How it works

- **Refresh.** The row updates when the session state changes and continuously while a turn runs, not on a timer. With the Python agent, a change is a new session, the start or the end of a turn, a model switch, or a rename of the session. An agent that sends status updates adds its own: a compaction, a HEAD move, an effort switch. An idle session does not re-run your script, so a clock in it will not tick on its own — unless you set `refresh_interval`, which adds a timer on top of all of the above. These updates are debounced at a fixed 300 ms, so a busy turn cannot run your script every frame; a change that must show at once (a resize, a new snapshot, switching agents) waits only 100 ms. A run already going is never cancelled: the next change waits for it to finish. `zypi` reads `[ui.status_line]` at startup, so changes to it take effect at the next launch.
- **Output.** Each line you print becomes one line of the row, up to five, and each is cut at 1024 characters, counting the ANSI escapes themselves, so a heavily coloured row has less room for text. A short terminal takes fewer, dropping the surplus from the bottom. ANSI colors are honored; every other escape (cursor motion, line erase, carriage-return overwrite) is dropped. OSC 8 hyperlinks are honored for `http`, `https` and `mailto` targets, and any other target renders as plain text. Stdout past 64 KiB is truncated and the script is stopped. A script that succeeds and prints nothing takes the row away rather than falling back to the built-in segments, so a script that prints only sometimes moves the transcript by a line as it comes and goes.
- **Sizing.** `zypi` sets `COLUMNS` and `LINES` to the row your output fills, not to the window: the pane's padding and your `padding` are already deducted. `tput` reports these too, since it reads them when stdout is not a terminal. `LINES` is what the row currently fills rather than what it may grow to, so it reads `1` until you print more; the ceiling is five whatever it says. Before the row has painted once, and on a frame with no room for it, the size is the last one the row painted at, or 80x1 if it never has.
- **Shell.** `command` is a shell command line, so `jq -r '…'` and pipes work as written; a path is run directly when it names an executable, and through `sh -c` otherwise, which is what runs a script whose `#!` line is missing or wrong. Quote a path containing spaces as you would at a prompt. Each run is a fresh process, so an edit to the script file applies on the next run.
- **Background work does not survive.** Whatever a script leaves running is killed when the run ends, on every path: a clean exit, a timeout, or too much output. The run ends when your script exits, so anything a background job prints after that is lost.
- **Environment.** Scripts run in the session's working directory, then the repository root, then the pager's own, whichever is a local path first, with a 10 second timeout, after which the row shows `[status line: timed out]`. `COLUMNS` and `LINES` describe the row the script fills, not the window. No shell rc files run (`BASH_ENV` and `ENV` are cleared), and `GIT_OPTIONAL_LOCKS=0`. Pagers and editors are neutralized the same way the rest of `zypi` neutralizes them, so a `git` or `gh` call inside your script will not block waiting for one.
- **Input.** The JSON payload is written to stdin with a trailing newline, so `read -r line` and `input=$(cat)` both work.

## Refresh runs

Set `refresh_interval` on a `command` row and the script also re-runs on a timer, so an incident page or a CI status can reach the row while the session sits idle:

```toml
[ui.status_line]
type = "command"
command = "~/.pi-python/statusline.sh"
refresh_interval = 300   # seconds
```

- **The payload says why the script ran.** A run that answers the timer carries `"trigger": "refresh_interval"` — a state change landing while a timer fire is owed rides that run — and a run with no fire owed carries `"trigger": "state"`. Hit the network on `refresh_interval` and read a cache on `state`, or a busy turn — which re-runs the script continuously — becomes a request storm against whatever the script calls.
- **The payload is as fresh as its source.** What `zypi` holds itself (the directory, the model, the session id, how long it has held the session, when the turn began) is read again for each run. The numbers an agent reports — cost, context, tokens — are the last ones it sent, so with an agent that sends status updates they are as of that update, not of the fire. The Python agent reports none. Only what your script fetches itself is always fresh.
- **Refresh failures keep the last output.** Once your script has answered — printed a row, or deliberately nothing — a timer run that fails or times out leaves the row exactly as it was, whether that is the last output or a failure a state run had already painted, and writes the failure to its log, so a flaky endpoint does not paint an error over a quiet night. Three consecutive refresh failures mean the script itself is broken, and the error shows after all; a refresh failure before the script has answered anything — a fresh session, or right after switching agents — also paints at once, since there is nothing to keep. A run triggered by session state still reports its failure at once, as ever.
- **Missed fires coalesce.** While the row is hidden (a fullscreen subagent view, the welcome screen) or a run already holds the slot, the fire waits and the row is owed one run when it can have it — never a burst for the fires a suspend or a long turn skipped. The timer keeps its cadence whatever your script's runtime: a fire that comes due while a run is still going is carried to the next run rather than stacked behind it.
- **The timer belongs to the mode that runs a script.** `refresh_interval` under `builtin` schedules nothing; under `disabled` it is off with everything else.

## Available data

Porting a script, read these closely. `workspace.repo_root` is the repository root, and there is no `project_dir`, a name used elsewhere for a launch directory. `context_window.session_usage` and the `session_*` token counts are cumulative for the session, not one call's, while the live window is `context_window.context_tokens`. There is no list of extra session directories, because `zypi` has none. `transcript_path` names the update stream file of an agent rather than a transcript in another tool's format, and `prompt_id` is present only while a turn runs. In each case a ported script reads nothing rather than a wrong answer, so guard the ones you use.

Nothing outside the table below is sent. A ported script that reads counts of lines the agent changed, a rate-limit summary, an editor mode, a thinking or fast-mode flag, an output style, a pull request, extra session directories, or the directory a worktree was created from will find them absent: each is either a feature `zypi` does not have or a number it cannot source honestly.

Where the fields come from: `zypi` fills these from what it knows itself, with any agent: `schema_version`, `version`, `trigger`, `cwd`, `workspace.current_dir`, `session_id`, `session_name`, `model.id` and `model.display_name` (once the model is known), `cost.total_duration_ms`, and `turn.started_at_ms` while a turn runs. Every other field comes from a status update that the agent sends. The Python agent sends none, so a script that runs with it never gets `prompt_id`, `transcript_path`, `effort`, `worktree`, `workspace.repo_root`, `workspace.branch`, `workspace.git_worktree`, `workspace.repo`, `cost.total_cost_usd`, `cost.total_api_duration_ms`, or any `context_window` field. To get the branch, the repository, or the worktree, run `git` in your script (see the [example](#example)). If an agent does send a status update, `zypi` uses its values first and fills only the fields that the agent left out. The session name is always the client's.

| Field | Description |
| --- | --- |
| `cwd`, `session_id` | Working directory and unique session id |
| `session_name` | The session's tab name, filled in by the client. Present in `command` stdin, absent from the `SessionStatus` notification |
| `prompt_id` | UUID of the prompt being processed. Present only during a turn |
| `transcript_path` | Path to the session's update file, from an agent that reports one. The file is that agent's own stream, so a script that parses another tool's transcript format will not read it |
| `model.id`, `model.display_name` | Model identifier and display name, as the session picker shows them. Omitted until the model is known |
| `workspace.current_dir` | Current directory |
| `workspace.repo_root` | The repository root, absent outside one. Not `project_dir`, a name used elsewhere for a launch directory |
| `workspace.branch` | Checked-out branch, in any repo. Absent on a detached HEAD |
| `workspace.git_worktree` | Worktree name, inside a linked worktree |
| `workspace.repo.{host,owner,name}` | Parsed from the `origin` remote, inside a git repo. `owner` is omitted for a remote with no owner segment |
| `schema_version` | Payload shape revision. Adding a field never bumps it; removing or retyping one does. Test it with `>=`, and branch on it rather than on `version` |
| `version` | `zypi` release, for display |
| `cost.total_duration_ms` | Milliseconds since this process attached the session. A resumed session counts from the resume, as its cost does |
| `cost.total_cost_usd`, `cost.total_api_duration_ms` | Session cost and API-wait milliseconds. The cost is absent until something in the session carries a price, and also when the usage ledger is unreadable, so treat an absent cost as unknown rather than as zero |
| `context_window.context_window_size` | Maximum context size, in tokens. Omitted until the model's window is known |
| `context_window.context_tokens` | Tokens the conversation occupies right now, counting input only, so it falls after a compaction. Omitted when the agent cannot read the count, so `0` always means an empty context |
| `context_window.session_input_tokens`, `.session_output_tokens` | Billed across the whole session, so they only grow. Named for the session because that is what they count: `total_*` is used elsewhere for what is in the window right now, which here is `context_tokens`. Dividing these by `context_window_size` passes 100% and keeps going. Omitted when the usage ledger is unreadable |
| `context_window.used_percentage`, `.remaining_percentage` | How full the window is right now, whole numbers from 0 to 100. Omitted with `context_window_size` or `context_tokens`, since a percentage of an unknown window is not a number |
| `context_window.session_usage.{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}` | `input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`, which sum back to `session_input_tokens`, plus `output_tokens`. Cumulative for the session, not one turn's. Absent before the first call |
| `context_window.auto_compact_threshold_percent` | Where the session auto-compacts. Omitted when the agent reported none |
| `effort.level` | Reasoning effort, when the model supports it |
| `turn.started_at_ms` | Unix milliseconds the turn in flight began, absent between turns. Subtract it from your own clock for an elapsed time |
| `worktree.{name,path,branch,main_worktree_root}` | Active worktree, inside a linked worktree. `name` is omitted for a worktree at a filesystem root, and `main_worktree_root` is where the worktree branched from |
| `trigger` | Why this run was invoked: `refresh_interval` for a run the timer asked for, `state` otherwise. Present on a command row's stdin, absent from the `SessionStatus` notification, which describes the session rather than a run |

Fields `zypi` cannot source are omitted rather than sent as placeholders, so the row never shows a fabricated value. Always guard them: `jq -r` prints the literal text `null` for a missing key, so write `// 0` or `// "?"` in jq, and `?.` in JavaScript.

## Example

Save a script (for example `~/.pi-python/statusline.sh`), make it executable with `chmod +x`, and set it as `command`. This one uses [`jq`](https://jqlang.org/); Python and Node.js parse JSON natively. The payload from the Python agent carries no branch and no dirty-file count, so the script calls `git` for both. The script runs in the working directory of the session (see [How it works](#how-it-works)), so `git` reads the right repository.

```bash
#!/bin/bash
input=$(cat)
DIR=$(echo "$input" | jq -r '.workspace.current_dir')
MODEL=$(echo "$input" | jq -r '.model.display_name // "?"')
BRANCH=$(git branch --show-current 2>/dev/null)
DIRTY=$(git diff --numstat 2>/dev/null | wc -l | tr -d ' ')
printf '%b\n' "${DIR##*/} │ $MODEL │ \033[32m${BRANCH:-detached}\033[0m ~$DIRTY"
```

## Tips

- Test with mock input: `echo '{"session_id":"t","workspace":{"current_dir":"/tmp/demo"},"model":{"display_name":"my-model"}}' | ./statusline.sh`
- Cache slow commands such as `git status` to a temp file keyed on `session_id`, refreshed every few seconds. `session_id` is stable per session and unique across sessions.
- Use `printf '%b'` rather than `echo -e` for reliable escapes.

## Troubleshooting

- **Nothing shows.** `zypi` reads `[ui.status_line]` at startup, so restart it after editing `config.toml`. The row only renders once the agent view is active, so not on the welcome screen. Check that `type` is not `disabled`, and that a command script is executable and writes to stdout. A built-in row that lists only `context` and `cost` is empty with the Python agent (see the note at the top of this page).
- **A message in the row.** A row beginning `[ui.status_line]` means `zypi` could not use that section as written: it either names the key it could not read, or names what the mode you chose still needs. Everything it could read still applies, and `zypi` leaves the section as you wrote it rather than rewriting one it cannot read. Setting `type = "disabled"` removes the row and the message.
- **A built-in row that shows less than you listed.** An item with no data is left out. The Python agent sends no status update, so `context` and `cost` never have data with it. List `cwd`, `model`, `turn-timer`, and `session-name` instead.
- **Only your own config can set this.** A `command` row runs a program, so `zypi` reads it from your own `~/.pi-python/config.toml`. A repository cannot set one, so cloning a repository cannot make `zypi` run its script.
- **Errors.** Anything your script prints is shown, even when it exits non-zero, so `printf …; [[ -n $dirty ]]` behaves as you would expect. A script that prints nothing and fails shows `[status line: exit N]`, and that stays until the next run succeeds — for a run triggered by session state, which reports its failure at once; a timer run's failure keeps the last output instead (see [Refresh runs](#refresh-runs)). Your script's stderr is never painted, so a debugging `echo` will not disturb the row; run `zypi` with `--debug` to read it. A script `zypi` could not start at all shows `[status line: could not start the script: …]`, which is what a file without the execute bit produces, and one the system kills shows `[status line: killed by signal]`. A `#!` line naming a missing interpreter is retried under `sh` instead, so it shows an exit code.
