"""Which processes survive each way of leaving zypi while the agent runs a long bash tool call?

Plan 1.R3 / the phase-1 exit condition ("no leftover Python or bash children after a normal exit,
a crash or Ctrl-C; Linux and macOS measured"). The agent is ``tests/_tool_agent.py``: its first
turn runs ``echo $$ > pidfile; exec sleep 300`` as a ``bash`` tool call, so the pid in the file is
the tool.

    exit_matrix.py [--zypi PATH] [--python PATH] [--sandbox PROFILE]
                   [--launch leader|shell|both] [--only ACTION[,ACTION]] [--repeat N]

``leader``: zypi is the session leader of the PTY (what the macOS runs did).
``shell``:  an interactive bash is the session leader and zypi is one of its jobs (a person running
            ``zypi`` from a prompt): own process group, no hang-up when it dies.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import pty_term as pt
import zypi_env as env

AGENT = env.AGENT_TESTS / "_tool_agent.py"
WATCH_SECONDS = 12.0

ACTIONS = ["ctrl-q-twice", "kill-9", "sigterm", "sighup", "close-terminal", "ctrl-c"]
# With --sandbox the process the terminal / shell launched is `bwrap`, and zypi is its child:
# `kill-9` / `sigterm` / `sighup` hit that outer process (what `kill $!` would do); this one hits
# the real zypi inside it.
SANDBOX_ONLY_ACTIONS = ["kill-9-inner"]

# Set from the command line in main().
ZYPI = ""
PY = ""


@dataclass
class Outcome:
    launch: str
    action: str
    sandbox: str = ""
    ready: bool = False
    zypi_status: str = ""
    gone_after: dict[str, float] = field(default_factory=dict)
    survivors: list[str] = field(default_factory=list)
    topology: str = ""
    names: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def clean(self) -> bool:
        return self.ready and not self.survivors


def find_zypi_pid(root_pid: int, deadline: float) -> int | None:
    """The first descendant of ``root_pid`` that is the zypi binary."""
    while time.monotonic() < deadline:
        for pid in pt.descendants(root_pid):
            if pt.cmdline(pid).split(" ")[0] == ZYPI:
                return pid
        time.sleep(0.1)
    return None


def find_outer_and_zypi(shell_pid: int, sandbox: bool, deadline: float) -> tuple[int, int] | None:
    """(the process the shell launched, the real zypi) under an interactive shell.

    Under ``--sandbox`` the launched process first *is* zypi (it parses its arguments), then
    ``exec``s ``bwrap``, which runs zypi again as its child: wait for the bwrap stage so the
    pre-exec zypi is not mistaken for the real one.
    """
    while time.monotonic() < deadline:
        for pid in pt.descendants(shell_pid):
            first = pt.cmdline(pid).split(" ")[0]
            if sandbox and first.rsplit("/", 1)[-1] == "bwrap":
                inner = find_zypi_pid(pid, deadline)
                return (pid, inner) if inner is not None else None
            if not sandbox and first == ZYPI:
                return pid, pid
        time.sleep(0.05)
    return None


def run_case(launch: str, action: str, sandbox: str = "") -> Outcome:
    out = Outcome(launch, action, sandbox)
    zypi_cmd = [ZYPI] + (["--sandbox", sandbox] if sandbox else [])
    root = env.scratch("pi-exit-")
    pidfile = root / "tool.pid"
    run_env = env.base_env(
        root,
        PS1="$ ",
        PI_AGENT_COMMAND=f"{PY} {AGENT}",
        PI_TEST_TOOL_PIDFILE=str(pidfile),
    )
    marker = env.marker(root)
    if launch == "leader":
        term = pt.Term(zypi_cmd, run_env, cwd=str(root / "work"))
        shell_pid = None
        # Without a sandbox the launched process *is* zypi; with one it is `bwrap`, zypi its child.
        outer_pid = term.proc.pid
        found = (
            term.proc.pid if not sandbox else find_zypi_pid(term.proc.pid, time.monotonic() + 15)
        )
    else:
        term = pt.Term(["bash", "--norc", "--noprofile", "-i"], run_env, cwd=str(root / "work"))
        shell_pid = term.proc.pid
        term.pump(0.6)
        term.type(" ".join(zypi_cmd) + "\r", 0.005)
        pair = find_outer_and_zypi(shell_pid, bool(sandbox), time.monotonic() + 15)
        outer_pid, found = pair if pair else (None, None)
    if found is None or outer_pid is None:
        out.note = "zypi did not start"
        term.kill_group()
        env.remove(root)
        return out
    zypi_pid = found
    try:
        out.ready = term.wait_for(r"Thanks for trying zypi", 25)
        if not out.ready:
            out.note = "TUI never became ready"
            return out
        term.type("go\r")
        deadline = time.monotonic() + 25
        tool_pid = None
        while time.monotonic() < deadline:
            term.pump(0.1)
            if pidfile.exists() and pidfile.read_text().strip().isdigit():
                pid = int(pidfile.read_text().strip())
                if pt.alive(pid):
                    tool_pid = pid
                    break
        if tool_pid is None:
            out.note = "the bash tool never started"
            return out
        agent_pid = next(
            (p for p in pt.descendants(zypi_pid) if "_tool_agent.py" in pt.cmdline(p)), None
        )
        if agent_pid is None:
            out.note = "agent process not found"
            return out
        pids = {"zypi": zypi_pid, "agent": agent_pid, "tool": tool_pid}
        if outer_pid != zypi_pid:
            pids = {"bwrap": outer_pid, **pids}
        out.topology = " ".join(f"{name}={pt.pgrp_sid(pid)}" for name, pid in pids.items())
        term.pump(0.5)

        t0 = time.monotonic()
        if action == "ctrl-q-twice":
            term.send("\x11")
            term.pump(0.3)
            term.send("\x11")
        elif action == "kill-9":
            os.kill(outer_pid, signal.SIGKILL)
        elif action == "kill-9-inner":
            os.kill(zypi_pid, signal.SIGKILL)
        elif action == "sigterm":
            os.kill(outer_pid, signal.SIGTERM)
        elif action == "sighup":
            os.kill(outer_pid, signal.SIGHUP)
        elif action == "close-terminal":
            term.close_master()
        elif action == "ctrl-c":
            term.send("\x03")
        else:
            raise ValueError(action)

        def watch(seconds: float, until_all: bool) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                term.pump(0.05)
                for name, pid in pids.items():
                    if name not in out.gone_after and not pt.alive(pid):
                        out.gone_after[name] = time.monotonic() - t0
                if until_all and len(out.gone_after) == len(pids):
                    break

        out.names = list(pids)
        watch(5.0 if action == "ctrl-c" else WATCH_SECONDS, until_all=action != "ctrl-c")
        if launch == "leader":
            # `alive()` counts a zombie as gone, so give `poll()` a moment to reap it before calling
            # the launched process "running" (close-terminal otherwise reads as a false positive).
            code = term.proc.poll()
            reap_by = time.monotonic() + 3.0
            while code is None and "zypi" in out.gone_after and time.monotonic() < reap_by:
                time.sleep(0.02)
                code = term.proc.poll()
            out.zypi_status = "running" if code is None else f"exit {code}"
        else:
            out.zypi_status = "gone" if "zypi" in out.gone_after else "running"

        if action == "ctrl-c":
            # Ctrl-C is "cancel the turn", not "quit": zypi and the agent stay, the tool goes.
            problems = []
            if "tool" not in out.gone_after:
                problems.append(f"tool pid {tool_pid} still running after Ctrl-C")
            problems += [
                f"{n} died on a single Ctrl-C" for n in pids if n != "tool" and n in out.gone_after
            ]
            out.note = "turn cancelled, zypi kept running" if not problems else ""
            # ... then leave properly and check nothing is left.
            term.type("/exit")
            term.pump(0.4)
            term.send("\r")
            watch(10.0, until_all=True)
            problems += [
                f"{n} pid {pids[n]} still running after /exit"
                for n in pids
                if n not in out.gone_after
            ]
            out.survivors = problems
        else:
            out.survivors = [
                f"{name} pid {pid} `{pt.cmdline(pid)[:40]}`"
                for name, pid in pids.items()
                if name not in out.gone_after
            ]
        # Anything else that still carries this run's PI_HOME (another agent, a stray tool)?
        extra = [
            f"pid {pid} `{cmd[:40]}`"
            for pid, _state, cmd in pt.processes_with_env(marker)
            if pid not in pids.values() and pid != shell_pid and pid != term.proc.pid
        ]
        if action != "ctrl-c" and extra:
            out.survivors += extra
        return out
    finally:
        term.kill_group()
        pt.kill_marked(marker)
        env.remove(root)


def describe(out: Outcome) -> str:
    if not out.ready:
        return f"NOT RUN ({out.note})"

    def t(name: str) -> str:
        return f"{out.gone_after[name]:.1f}s" if name in out.gone_after else "STILL RUNNING"

    gone = "; ".join(f"{n} gone {t(n)}" for n in out.names if n != "zypi" or out.sandbox)
    return (
        f"launched process {out.zypi_status}; {gone}"
        + (f"; {out.note}" if out.note else "")
        + ("" if out.clean else f"  <-- LEFTOVER: {', '.join(out.survivors)}")
    )


def main() -> int:
    global ZYPI, PY
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument(
        "--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON, then this)"
    )
    ap.add_argument("--launch", choices=["leader", "shell", "both"], default="both")
    ap.add_argument("--only", default="", help="comma-separated actions to run")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument(
        "--sandbox", default="", help="run zypi with --sandbox PROFILE (Linux: under bwrap)"
    )
    args = ap.parse_args()
    ZYPI = env.find_zypi(args.zypi)
    PY = env.agent_python(args.python)
    if not Path(AGENT).is_file():
        raise SystemExit(f"stand-in agent missing: {AGENT}")
    actions = [a for a in args.only.split(",") if a] or ACTIONS + (
        SANDBOX_ONLY_ACTIONS if args.sandbox else []
    )
    launches = ["leader", "shell"] if args.launch == "both" else [args.launch]
    bad = 0
    rows = []
    for launch in launches:
        for action in actions:
            for i in range(args.repeat):
                out = run_case(launch, action, args.sandbox)
                rows.append(out)
                flag = "ok  " if out.clean else "LEAK"
                print(f"[{flag}] {launch:6} {action:14} #{i + 1}: {describe(out)}", flush=True)
                if out.topology:
                    print(f"         (pgrp, session): {out.topology}", flush=True)
                bad += 0 if out.clean else 1
    print(f"\n{len(rows) - bad}/{len(rows)} cases left nothing behind")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
