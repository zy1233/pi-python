"""Functional smoke of zypi + pi_agent_cli on Linux, under a PTY (mock LLM, throw-away PI_HOME).

    smoke.py [--zypi PATH] [--python PATH] [--sandbox PROFILE] [--show]

``--show`` prints the screen after every step. ``--sandbox`` starts zypi with that profile and
checks what the status bar says about it.

Covers what the plan's PTY checks covered on macOS: welcome page, one turn, /model switching
(ACP session config option), a clean /exit, then a restart in the same home with /resume and the
replayed history; plus the agent's stderr log. Exit status 0 only when every step passed.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
import time
from pathlib import Path

import pty_term as pt
import zypi_env as env

results: list[tuple[str, bool, str]] = []
SHOW = False


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return ok


def show(term: pt.Term, title: str) -> None:
    if SHOW:
        print(f"----- {title}")
        print(term.text())
        print("-----")


def status_line(term: pt.Term) -> str:
    """The label on the input box's bottom border, e.g. ``mock-a · auto`` or ``Second model``."""
    for line in reversed(term.text().splitlines()):
        stripped = line.strip()
        if stripped.startswith("╰") and stripped.endswith("╯"):
            return stripped.strip("╰╯─ ")
    return ""


def landlock_listed() -> bool:
    """Whether the kernel lists Landlock among its security modules (5.13+, and enabled)."""
    try:
        return "landlock" in Path("/sys/kernel/security/lsm").read_text()
    except OSError:
        return False


def check_stderr_reaches_the_log(zypi: str) -> None:
    """The real agent says nothing on stderr when all is well, so use one that always does."""
    root = env.scratch("pi-smoke-stderr-")
    run_env = env.base_env(
        root, PI_AGENT_COMMAND="bash -c 'echo STDERR-MARK >&2; sleep 30' pi-agent"
    )
    log = root / "home" / "logs" / "agent.stderr.log"
    term = None
    try:
        term = pt.Term([zypi], run_env, cwd=str(root / "work"))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            term.pump(0.2)
            if log.exists() and "STDERR-MARK" in log.read_text(encoding="utf-8"):
                break
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        check("what the agent writes to stderr lands in the log", "STDERR-MARK" in text, text[:80])
        check(
            "none of it is drawn over the TUI",
            "STDERR-MARK" not in (term.text() if term else ""),
        )
    finally:
        if term is not None:
            term.kill_group()
        pt.kill_marked(env.marker(root))
        env.remove(root)


def main() -> int:
    global SHOW
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument(
        "--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON, then this)"
    )
    ap.add_argument("--sandbox", default="", help="start zypi with --sandbox PROFILE")
    ap.add_argument("--show", action="store_true", help="print the screen after every step")
    args = ap.parse_args()
    SHOW = args.show
    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    argv = [zypi] + (["--sandbox", args.sandbox] if args.sandbox else [])

    root = env.scratch("pi-smoke-")
    home = root / "home"
    (home / "agent.toml").write_text(
        '[model]\nprovider = "mock"\nid = "mock-a"\n\n'
        '[[models]]\nid = "mock-b"\nname = "Second model"\n',
        encoding="utf-8",
    )
    marker = env.marker(root)
    run_env = env.base_env(root, PI_PYTHON=python, PI_USE_MOCK="1")

    def start() -> pt.Term:
        return pt.Term(argv, run_env, cwd=str(root / "work"))

    term = start()
    try:
        # 1. welcome page
        ready = term.wait_for(r"Thanks for trying zypi", 20)
        check("welcome page renders", ready)
        show(term, "welcome")
        check(
            "no panic / traceback on screen",
            not any(w in term.text() for w in ("panicked", "Traceback")),
        )
        agent = [p for p in pt.descendants(term.proc.pid) if "pi_agent_cli" in pt.cmdline(p)]
        check("zypi spawned exactly one Python agent", len(agent) == 1, f"pids={agent}")

        # 1b. the agent's stderr is kept: the TUI owns the screen and its own stderr is /dev/null
        log = home / "logs" / "agent.stderr.log"
        starts = log.read_text(encoding="utf-8").count("--- agent started") if log.exists() else 0
        check("the agent's stderr log is started under PI_HOME/logs", starts == 1, str(log))
        if log.exists():
            mode = stat.S_IMODE(log.stat().st_mode)
            check("the stderr log is owner-only", mode == 0o600, oct(mode))

        # 1c. a sandbox that cannot be enforced must say so (Linux without Landlock)
        if args.sandbox:
            text = term.text()
            if landlock_listed():
                check(
                    f"status bar shows sandbox:{args.sandbox} (kernel lists Landlock)",
                    f"sandbox:{args.sandbox}" in text,
                    "enforced or not-enforced label",
                )
            else:
                check(
                    "welcome page says the sandbox is not enforced (kernel has no Landlock)",
                    f"sandbox:{args.sandbox} (not enforced)" in text,
                )
                check(
                    "the start-up warning was written to the terminal",
                    b"could not be put in force" in bytes(term.raw),
                )

        # 2. one turn
        term.type("hello\r")
        check("a prompt gets the mock reply", term.wait_for(r"Hello from mock", 20))
        show(term, "after first turn")
        check(
            "status line names the default model", "mock-a" in status_line(term), status_line(term)
        )
        if args.sandbox and not landlock_listed():
            check(
                "the session status bar says so too",
                f"sandbox:{args.sandbox} (not enforced)" in term.text(),
            )

        # 3. /model: the picker lists both configured models, picking one switches it
        term.type("/model")
        term.pump(0.6)
        show(term, "slash menu for /model")
        term.send("\r")
        opened = term.wait_for(r"Second model", 10)
        show(term, "model picker")
        check("/model picker lists the [[models]] entry", opened)
        if opened:
            term.send("\x1b[B")  # down: onto "Second model"
            term.pump(0.4)
            term.send("\r")
            switched = term.wait_for(r"Second model\s+·", 10)
            show(term, "after choosing")
            check("status line follows the switch", switched, status_line(term))
        term.type("again\r")
        # The view scrolls to the newest prompt, so the earlier turn is no longer on screen.
        answered = term.wait_for(r"(?s)again.*Hello from mock.*Worked for", 20)
        show(term, "after second turn")
        check("a turn after the switch still answers", answered)

        # 4. clean /exit
        term.type("/exit")
        term.pump(0.5)
        term.send("\r")
        code = term.wait_exit(15)
        check("/exit exits with status 0", code == 0, f"status={code}")
        left = pt.processes_with_env(marker)
        check("no agent process left after /exit", not left, str(left))

        # 5. the session file carries the model change
        files = sorted((home / "sessions").glob("*.jsonl"))
        check("one session file written", len(files) == 1, str(files))
        if files:
            lines = files[0].read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line]
            changes = [r for r in records if r.get("type") == "model_change"]
            check(
                "model_change persisted with the chosen model",
                any("mock-b" in json.dumps(r) for r in changes),
                f"{len(changes)} model_change record(s)",
            )

        # 6. restart in the same home: /resume restores the session and its model
        term.kill_group()
        term = start()
        check("second start renders", term.wait_for(r"Thanks for trying zypi", 20))
        starts = log.read_text(encoding="utf-8").count("--- agent started") if log.exists() else 0
        check("the second run appends to the same stderr log", starts == 2, f"{starts} start(s)")
        term.type("/resume")
        term.pump(0.6)
        term.send("\r")
        picker = term.wait_for(r"hello", 10)
        show(term, "resume picker")
        check("/resume lists the earlier session by its first message", picker)
        if picker:
            # first row is the fresh empty session (plan 10.7); the one to restore is below it
            term.send("\x1b[B")
            term.pump(0.4)
            term.send("\r")
            replayed = term.wait_for(r"Hello from mock", 15)
            show(term, "after resume")
            check("resumed session replays its history", replayed and "again" in term.text())
            check(
                "resumed session keeps the chosen model",
                "Second model" in status_line(term),
                status_line(term),
            )
        term.type("/exit")
        term.pump(0.5)
        term.send("\r")
        code = term.wait_exit(15)
        check("second /exit exits with status 0", code == 0, f"status={code}")
        left = pt.processes_with_env(marker)
        check("no agent process left", not left, str(left))

        # 7. what the agent writes to stderr lands in the log (a stand-in agent: no ACP needed)
        check_stderr_reaches_the_log(zypi)
    finally:
        term.kill_group()
        pt.kill_marked(marker)
        env.remove(root)
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    t0 = time.monotonic()
    rc = main()
    print(f"({time.monotonic() - t0:.0f}s)")
    sys.exit(rc)
