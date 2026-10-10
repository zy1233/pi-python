# PTY checks for zypi on Linux

Drivers that run the real `zypi` binary under a pseudo-terminal (a [pyte](https://pypi.org/project/pyte/)
screen reads what it draws) and look at the process tree it leaves. They are the Linux counterpart of
the macOS PTY runs in `docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md` (sections 10.7 and 10.10). They need
a built `zypi`, so they are not part of `pytest` or CI.

| Script | What it checks |
|--------|----------------|
| `smoke.py` | Welcome page, one turn, `/model`, `/exit`, then a restart in the same home with `/resume`; the agent's stderr log (`$PI_HOME/logs/agent.stderr.log`, owner-only, appended per run); with `--sandbox PROFILE`, what the status bar says about the profile |
| `exit_matrix.py` | While the agent runs a long `bash` tool call, leave zypi six ways (`/exit` twice, `kill -9`, `SIGTERM`, `SIGHUP`, closing the terminal, Ctrl-C) from two launch styles (zypi as session leader, zypi as a job of an interactive shell); nothing may be left running |
| `print_exit.py` | `zypi -p ...` while only zypi gets `SIGTERM` / `SIGKILL` / `SIGHUP` / `SIGINT`: the agent and its tool call must stop too |
| `resume_matrix.py` | What `--continue`, `--resume <id>`, `--resume <title>`, `--session-id` and `zypi export` do with a session the Python agent wrote (13 cases). Four are **known gaps** (reported as `GAP`, not failures; `--strict` fails them): the pager still reads the old Rust agent's session files for them. Landing ADR1 (plan section 10.11) has to close them and drop each case's `gap` text; a gap that has closed shows as `FIXED` |
| `mode_probe.py` | Which permission modes the TUI offers (Shift+Tab cycle, `--always-approve`) the Python agent honours: for each, whether a `write` is asked about and whether it is carried out unasked. Judges nothing, prints a table (plan section 10.11, appendix A, 1.P3): today Plan restricts nothing and Auto never asks |

`pty_term.py` is the driver (`Term`, plus `/proc` helpers); `zypi_env.py` finds the binary and builds
a throw-away home.

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

## WSL

Build and run on the Linux file system (`~/`), not under `/mnt/c` or `/mnt/d`: a build there is slow
and file-locking and permission checks behave differently. WSL2 kernels before 5.13 have no Landlock,
so `--sandbox` shows `sandbox:<profile> (not enforced)` in the status bar and the file confinement
is not in force; `smoke.py` checks for exactly that when the kernel does not list Landlock.
