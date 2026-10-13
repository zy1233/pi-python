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
| `resume_matrix.py` | What `--continue`, `--resume <id>`, `--resume <title>`, `--session-id` and `zypi export` do with a session the Python agent wrote (13 cases). Two are **known gaps** (reported as `GAP`, not failures; `--strict` fails them): the pager still reads the old Rust agent's session files for them (`--session-id <fresh uuid>` closed when the agent began to honour `_meta.sessionId`; the two `zypi export` cases closed when `zypi export` began to start an agent and replay the session with `session/load`: they check the transcript on stdout, an error that names an unknown id, and that no agent is left running). Landing ADR1 (execution log section 10.11) has to close the two that are left and drop each case's `gap` text; a gap that has closed shows as `FIXED` |
| `mode_probe.py` | Which permission modes the TUI offers (Shift+Tab cycle, `--always-approve`, `--permission-mode default\|bypassPermissions`) the Python agent honours: for each, whether a `write` is asked about and whether it is carried out unasked; then the four `--permission-mode` values that are refused (`acceptEdits`, `auto`, `dontAsk`, `plan`), each of which must exit at once with the reason; last, the settings page (does a search for "plan" find a "Plan mode" row, which choices does the Permission mode picker list). Judges nothing, prints a table (execution log section 10.13; plan appendix A, 1.P3 (a)): Plan, Auto and the settings page's Default are hidden because the agent honours neither (section 10.11 has the measurements from before they were hidden) |
| `net_audit.py` | Whether zypi talks to the network (plan C.1). It plays one turn and, every few milliseconds, lists the sockets of zypi and of each process below it (`/proc/<pid>/fd` joined with `/proc/<pid>/net/*`). zypi must have no connection to a non-loopback address, and it must listen on none. `--mock` uses the mock LLM (the agent must be silent too). `--model-from FILE` plays one real turn with the `[model]` table of that `agent.toml`: the agent must then hold an established connection to a non-loopback address (the positive control, which shows that the sampler can see traffic), and the output names no host, address or key. `--no-network` (with `--mock`) runs everything in an empty network namespace (`bwrap --unshare-net`): the turn must still end |
| `status_line_probe.py` | Whether the status row is drawn (`[ui.status_line]`, `type = "command"` and `"builtin"`). With the plain Python agent the row must not be drawn, and a command script must not run. With `status_proxy_agent.py`, which adds the `session_status` message that the old Rust agent sent, the row must be drawn: this second run is the control that shows the config text is right. Execution log section 10.14 has the result |

`pty_term.py` is the driver (`Term`, plus `/proc` helpers); `zypi_env.py` finds the binary and builds
a throw-away home. `status_proxy_agent.py` is a stand-in agent: the Python agent plus one extra message.
`status_line_probe.py` starts it through `PI_AGENT_COMMAND`.

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
