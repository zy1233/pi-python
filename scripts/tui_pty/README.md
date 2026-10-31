# PTY checks for zypi on Linux

Drivers that run the real `zypi` binary under a pseudo-terminal (a [pyte](https://pypi.org/project/pyte/)
screen reads what it draws) and look at the process tree it leaves. They are the Linux counterpart of
the macOS PTY runs in `docs/PLAN/archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md` (sections 10.7 and 10.10). They need
a built `zypi`, so they are not part of `pytest` or CI.

| Script | What it checks |
|--------|----------------|
| `smoke.py` | Welcome page, one turn, `/model`, `/exit`, then a restart in the same home with `/resume`; the agent's stderr log (`$PI_HOME/logs/agent.stderr.log`, owner-only, appended per run); with `--sandbox PROFILE`, what the status bar says about the profile |
| `exit_matrix.py` | While the agent runs a long `bash` tool call, leave zypi six ways (`/exit` twice, `kill -9`, `SIGTERM`, `SIGHUP`, closing the terminal, Ctrl-C) from two launch styles (zypi as session leader, zypi as a job of an interactive shell); nothing may be left running |
| `print_exit.py` | `zypi -p ...` while only zypi gets `SIGTERM` / `SIGKILL` / `SIGHUP` / `SIGINT`: the agent and its tool call must stop too |
| `resume_matrix.py` | What `--continue`, `--resume <id>`, `--resume <title>`, `--session-id` and `zypi export` do with a session the Python agent wrote (16 cases). The pager asks the agent over ACP (`session/list`, then `session/load`) and reads no session file (ADR1, execution log section 10.16), so every case has to hold: an unknown id and a used `--session-id` end with exit 1 before the TUI is up, and the export cases check the transcript on stdout, an error that names an unknown id, and that no agent is left running. The agent writes a new session's file with the first message, not when the session starts (plan decision 17). The TUI starts a session at once, with no message, for `--session-id` and for `/new`, so two cases leave such a session with no message and want no session file, and two cases type a message and want the file after it. A case can have steps: the run types the first one when the TUI is up (the model name is on the border of the input box), and each next one when the screen shows that the one before took effect. A case that is known not to hold carries a `gap` text and is reported as `GAP` (not a failure; `--strict` fails it); a gap that has closed shows as `FIXED`. None has a gap now |
| `mode_probe.py` | Which permission modes the TUI offers (Shift+Tab cycle, `--always-approve`, `--permission-mode default\|bypassPermissions`) the Python agent honours: for each, whether a `write` is asked about and whether it is carried out unasked; then the four `--permission-mode` values that are refused (`acceptEdits`, `auto`, `dontAsk`, `plan`), each of which must exit at once with the reason; last, the settings page (does a search for "plan" find a "Plan mode" row, which choices does the Permission mode picker list). Judges nothing, prints a table (execution log section 10.13; plan appendix A, 1.P3 (a)): Plan, Auto and the settings page's Default are hidden because the agent honours neither (section 10.11 has the measurements from before they were hidden) |
| `net_audit.py` | Whether zypi talks to the network (plan C.1). It plays one turn and, every few milliseconds, lists the sockets of zypi and of each process below it (`/proc/<pid>/fd` joined with `/proc/<pid>/net/*`). zypi must have no connection to a non-loopback address, and it must listen on none. `--mock` uses the mock LLM (the agent must be silent too). `--model-from FILE` plays one real turn with the `[model]` table of that `agent.toml`: the agent must then hold an established connection to a non-loopback address (the positive control, which shows that the sampler can see traffic), and the output names no host, address or key. `--no-network` (with `--mock`) runs everything in an empty network namespace (`bwrap --unshare-net`): the turn must still end |
| `status_line_probe.py` | Whether the status row is drawn from the right facts (`[ui.status_line]`, `type = "command"` and `"builtin"`). The Python agent sends no status, so with the plain agent the row is built from what zypi knows (directory, session id, model): it must be drawn with the model of the session, and a command script must run with a payload that names that model and session and has no number zypi cannot know (a context percentage, a cost). With `status_proxy_agent.py`, which adds a `session_status` message of its own, the row must show the model that message names: this second run shows that a status from the agent is read first. Execution log section 10.14 has the first result (the row stayed empty); section 10.15 has the change |

| `flags_probe.py` | Which command-line flags of the interactive `zypi` change anything for the Python agent (plan 1.P6, decision 8). It runs zypi once without the flag and once with it, in a throw-away home, and prints what is different: the ACP lines zypi sent, the agent's arguments and variables, the model, the tools and the system prompt of the LLM call, the label on the prompt box, the files that appeared, or that zypi refused to start (exit status and first line). `NO CHANGE` means that the flag does not reach the agent; a flag that works inside zypi (`--no-alt-screen`) or writes a file (`--debug-file`) shows in `label` and `files`. About 65 flags, about 5 minutes. Judges nothing; `-p` has its own contract (`print_mode.rs`) and is not run |
| `settings_probe.py` | The same question for the settings page (plan decision 14): the rows that the page offers; the permission question with `remember_tool_approvals` unset, on and off; the row that the cursor is on for each value of `default_selected_permission`; whether `[models].default` is the model of the next start; whether a config file that sets the other settings meant for the old agent changes anything on the wire. Judges nothing |

`pty_term.py` is the driver (`Term`, plus `/proc` helpers); `zypi_env.py` finds the binary and builds
a throw-away home. `status_proxy_agent.py` is a stand-in agent: the Python agent plus a status message of its own.
`status_line_probe.py` starts it through `PI_AGENT_COMMAND`.

`wire_probe.py` is what `flags_probe.py` and `settings_probe.py` share: `execute` runs one zypi, types a
prompt, answers the permission question and returns what the two ends saw. zypi gives the Python agent
no argument and no variable but `PI_HOME`, so a flag or a setting that does not show up in the ACP lines
cannot change what the agent does. `wire_log_agent.py` is the stand-in agent that copies the ACP lines
both ways into a log; it starts `context_spy_agent.py`, which is the Python agent with each LLM call recorded
(model, reasoning option, tools, system prompt) and a scripted tool call (`PI_TEST_TOOL_CALL`).

## Running

Linux only (they use `pty`, `termios` and `/proc`). From the repository root:

```bash
pip install pyte                       # into the interpreter that runs the scripts
(cd tui && cargo build -p pi-pager-bin)   # or pass any zypi with --zypi

python scripts/tui_pty/smoke.py --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/exit_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/print_exit.py  --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/resume_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/mode_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/status_line_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/flags_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python   # --only TEXT, --list, --raw DIR
python scripts/tui_pty/settings_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python   # --list: only the rows
python scripts/tui_pty/net_audit.py --mock --zypi tui/target/debug/zypi --python .venv/bin/python
python scripts/tui_pty/net_audit.py --mock --no-network --zypi tui/target/debug/zypi --python .venv/bin/python
# One real turn. The key comes from the environment, never from the command line:
REAL_LLM_API_KEY=... python scripts/tui_pty/net_audit.py --model-from ~/.pi-python/agent.toml \
    --zypi tui/target/debug/zypi --python .venv/bin/python
```

- `--zypi` falls back to `$ZYPI`, then `tui/target/{debug,release}/zypi`. Cargo writes somewhere else
  when `CARGO_TARGET_DIR` is set, so pass the path in that case.
- `--python` is the interpreter with `pi_agent_cli` installed (`PI_PYTHON`); it falls back to
  `$PI_PYTHON`, then the interpreter running the script.
- Add `--sandbox workspace` to `smoke.py` or `exit_matrix.py` to run zypi under a sandbox profile
  (on Linux that means `bwrap`; the kernel layer needs Landlock, 5.13+).
- `exit_matrix.py --repeat N` runs every case N times (timing-sensitive cases are worth repeating);
  `--only kill-9,sigterm` and `--launch leader|shell` narrow it down.
- Each script exits 0 only when every check passed (`resume_matrix.py` tolerates its known gaps
  unless `--strict`; `--wait SECONDS` is how long it gives each start).

Every run uses a fresh temporary `PI_HOME`, `HOME` and working directory, and the environment is built
from scratch: what your shell exports (`PI_HOME`, `PI_AGENT_COMMAND`, ...) is not passed on, so the
scripts never touch a real `~/.pi-python`. A run that is interrupted may leave processes behind; they
are found by their `PI_HOME=/tmp/pi-...` environment entry.

`net_audit.py --model-from FILE` is the one exception. It copies only the `[model]` table of FILE into the
temporary home (a key that is written in the table stays behind), and it passes on one variable from your
environment: the one that `api_key_env` in that table names. It reads no `local.env`.

## WSL

Build and run on the Linux file system (`~/`), not under `/mnt/c` or `/mnt/d`: a build there is slow
and file-locking and permission checks behave differently. WSL2 kernels before 5.13 have no Landlock,
so `--sandbox` shows `sandbox:<profile> (not enforced)` in the status bar and the file confinement
is not in force; `smoke.py` checks for exactly that when the kernel does not list Landlock.
