"""What the flags that look for an earlier session do with a session the Python agent wrote.

    resume_matrix.py [--zypi PATH] [--python PATH] [--strict] [--wait SECONDS] [--show]

Plan 0.7 / ADR1. The pager used to read the session files of the old Rust agent to resolve
``--continue``, ``--resume <title>``, ``--session-id`` and ``zypi export``; the Python agent writes
a different layout, so each flag worked on a Python session only by accident, by falling back to
the agent, or not at all. Now the pager asks the agent over ACP (``session/list``, then
``session/load``) and reads no session file. This script makes one session through the TUI (mock
model, first message "remember this"), starts zypi again with each flag and says whether it did
what its help text promises.

The agent writes a new session's file with the first message, not when the session starts (plan
decision 17). The TUI makes a session at once (with no message) for ``--session-id`` and for
``/new``, so two cases leave such a session without a message and want no file. The first of them
comes first on purpose: an agent that wrote the file at once would leave an empty session that is
the newest one, ``--continue`` and ``--resume`` with no argument would take it, and those cases
would fail too. The cases that do type a message (``--session-id <fresh uuid>``, ``/new`` and then
a message) look for the file after it. The two ``/new`` cases start with ``--resume <id>``, so
they do not depend on which session is the newest.

Every case has to hold. A case that is known not to hold carries a ``Case.gap`` text that says
what is wrong; it is reported as GAP and does not fail the run, and ``--strict`` makes it fail. A
gap that has closed shows as FIXED until its text is dropped. No case has a gap at present. Exit
status 0 when nothing FAILed.

Linux only (it needs a pty); everything but ``run`` is plain Python and is tested on any platform.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zypi_env as env

FIRST = "remember this"  # the first message of the session that is resumed (so: its title)
REPLY = "Hello from mock"  # what the mock model answers


@dataclass
class Outcome:
    """What one run did."""

    code: int | None  # exit status; None while zypi was still running when we looked
    text: str  # the screen, or what a run that ended printed
    stdout: str = ""  # `zypi export` only
    new_sessions: dict[str, str] = field(default_factory=dict)  # file name -> id in its header
    # `zypi export` only: the processes of the run that were still alive when it ended
    left_behind: list[str] = field(default_factory=list)

    @property
    def replayed(self) -> bool:
        """The earlier conversation is on the screen."""
        return FIRST in self.text and REPLY in self.text

    @property
    def failed(self) -> bool:
        return self.code not in (None, 0)


Ids = dict[str, str]  # existing / unknown / fresh / idle


@dataclass(frozen=True)
class Step:
    """Typed (and sent) in the TUI, and what the screen shows once it has taken effect."""

    text: str
    done: str  # a regex


@dataclass(frozen=True)
class Case:
    name: str
    argv: list[str]  # `{existing}`, `{unknown}`, `{fresh}`, `{idle}` and `{title}` are filled in
    cwd: str  # "work" (where the session was made) or "other" (a directory without sessions)
    expect: str  # what a user is promised
    holds: Callable[[Outcome, Ids], bool]
    gap: str = ""  # not empty: known not to hold today, and why
    export: bool = False  # run as a plain command, not in the TUI
    # A regex for what the screen shows once the TUI is up. Without it a run is over when the
    # earlier conversation is on the screen or zypi has exited, so a run that shows neither (a
    # new session) would use up the whole wait.
    ready: str = ""
    # Typed one after the other, the first once the TUI is up and each next one once the screen
    # shows that the one before took effect. The run is over when the last one has.
    steps: tuple[Step, ...] = ()


# What the TUI shows once the agent has answered ``session/new``: the name of the model on the
# border of the input box (it comes with the answer, so the session exists in the agent by then).
# The welcome text would not do: now and then it is gone from the screen before a poll sees it,
# and a message typed before the TUI is up is lost. (A start with no flag makes no session until
# the first message, so this never shows there.)
UP = r"mock ─╯"
# A resumed session is up when its conversation is on the screen as well.
UP_RESUMED = rf"(?s)(?=.*{REPLY})(?=.*{UP})"
# What ``/new`` prints once the agent has answered ``session/new`` for it.
NEW_SESSION = r"use /resume to switch between sessions"
# The second message of a session, and the reply to it below. (The reply alone would also match
# the conversation of a session that is resumed.)
SECOND = "second message"
ANSWER = rf"(?s){SECOND}.*{REPLY}"

CASES: list[Case] = [
    Case(
        "--session-id <fresh uuid>, left with no message, leaves no session file",
        ["--session-id", "{idle}"],
        "work",
        "the session exists, and no new session file: it is saved with its first message",
        lambda o, ids: o.code is None and bool(re.search(UP, o.text)) and not o.new_sessions,
        ready=UP,
    ),
    Case(
        "--continue resumes the project's latest session",
        ["--continue"],
        "work",
        "the conversation is replayed",
        lambda o, ids: o.replayed,
    ),
    Case(
        "--continue with no session in the directory says so",
        ["--continue"],
        "other",
        "exit 1, 'No session found'",
        lambda o, ids: o.code == 1 and "No session found" in o.text,
    ),
    Case(
        "--resume <id> in the session's directory",
        ["--resume", "{existing}"],
        "work",
        "the conversation is replayed",
        lambda o, ids: o.replayed,
    ),
    Case(
        "--resume <id> from another directory",
        ["--resume", "{existing}"],
        "other",
        "the conversation is replayed",
        lambda o, ids: o.replayed,
    ),
    Case(
        "--resume <title> in the session's directory",
        ["--resume", "{title}"],
        "work",
        "the conversation is replayed",
        lambda o, ids: o.replayed,
    ),
    Case(
        "--resume <title> from a directory without that session",
        ["--resume", "{title}"],
        "other",
        "exit 1, 'no session id or title matched' (titles are matched per directory)",
        lambda o, ids: o.code == 1 and "no session id or title matched" in o.text,
    ),
    Case(
        "--resume with no argument",
        ["--resume"],
        "work",
        "the conversation is replayed",
        lambda o, ids: o.replayed,
    ),
    Case(
        "/new, left with no message, leaves no session file",
        ["--resume", "{existing}"],
        "work",
        "the new session exists, and no new session file: it is saved with its first message",
        lambda o, ids: (
            o.code is None and bool(re.search(NEW_SESSION, o.text)) and not o.new_sessions
        ),
        ready=UP_RESUMED,
        steps=(Step("/new", NEW_SESSION),),
    ),
    Case(
        "--resume <unknown id> names the id in an error",
        ["--resume", "{unknown}"],
        "work",
        "exit 1 before the TUI is up, an error that names the id; nothing replayed",
        lambda o, ids: o.code == 1 and ids["unknown"] in o.text and not o.replayed,
    ),
    Case(
        "--resume <unknown title> exits with an error",
        ["--resume", "no such title"],
        "work",
        "exit 1, 'no session id or title matched'",
        lambda o, ids: o.code == 1 and "no session id or title matched" in o.text,
    ),
    Case(
        "--session-id <fresh uuid> starts a session with that id",
        ["--session-id", "{fresh}"],
        "work",
        "after a first message, a new session file whose header carries the id",
        lambda o, ids: ids["fresh"] in o.new_sessions.values(),
        ready=UP,
        steps=(Step(SECOND, ANSWER),),
    ),
    Case(
        "/new, then a message, saves the new session",
        ["--resume", "{existing}"],
        "work",
        "after the first message of the new session, one new session file, not the earlier one",
        lambda o, ids: len(o.new_sessions) == 1 and ids["existing"] not in o.new_sessions.values(),
        ready=UP_RESUMED,
        steps=(Step("/new", NEW_SESSION), Step(SECOND, ANSWER)),
    ),
    Case(
        "--session-id <id in use> is refused",
        ["--session-id", "{existing}"],
        "work",
        "exit 1, 'already in use', no new session",
        lambda o, ids: o.code == 1 and "already in use" in o.text and not o.new_sessions,
    ),
    Case(
        "export <id> prints the transcript",
        ["export", "{existing}"],
        "work",
        "exit 0, both sides of the conversation on stdout, no agent left running",
        lambda o, ids: (
            o.code == 0 and FIRST in o.stdout and REPLY in o.stdout and not o.left_behind
        ),
        export=True,
    ),
    Case(
        "export <unknown id> fails without output",
        ["export", "{unknown}"],
        "work",
        "exit non-zero, an error that names the id, nothing on stdout, no agent left running",
        lambda o, ids: (
            o.failed
            and not o.stdout.strip()
            and ids["unknown"] in o.text
            and "not found" in o.text
            and not o.left_behind
        ),
        export=True,
    ),
]


def judge(case: Case, outcome: Outcome, ids: Ids, strict: bool = False) -> str:
    """PASS, FAIL, GAP (known gap, still open) or FIXED (known gap that no longer is one)."""
    holds = case.holds(outcome, ids)
    if not case.gap:
        return "PASS" if holds else "FAIL"
    if holds:
        return "FIXED"
    return "FAIL" if strict else "GAP"


def sessions(home: Path) -> dict[str, str]:
    """The session files in ``home``, by name, with the id in each one's header line."""
    found: dict[str, str] = {}
    for path in sorted((home / "sessions").glob("*.jsonl")):
        try:
            found[path.name] = str(json.loads(path.read_text("utf-8").splitlines()[0]).get("id"))
        except (OSError, ValueError, IndexError):
            found[path.name] = "<unreadable>"
    return found


def excerpt(text: str, lines: int = 4) -> str:
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    return " | ".join(rows[:lines]) + (" | ..." if len(rows) > lines else "")


def fill(argv: list[str], ids: Ids) -> list[str]:
    return [part.format(title=FIRST, **ids) for part in argv]


def wait_for_run(term: Any, case: Case, wait: float) -> None:
    """Pump ``term`` until the run of ``case`` is over, or ``wait`` seconds have gone.

    Over means: zypi has exited; or, for a case with steps, the last step has taken effect; or the
    earlier conversation is on the screen; or the TUI is up (``case.ready``). A case with steps
    waits for the TUI to be up, and for the screen to show each step before it goes on.
    """
    end = time.monotonic() + wait
    todo = list(case.steps)
    pending = ""  # what the screen has to show for the step that was typed last
    started = False  # the first step was typed
    while time.monotonic() < end:
        term.pump(0.3)
        text = term.text()
        if pending and re.search(pending, text) is None:
            if term.proc.poll() is not None:
                return
            continue
        pending = ""
        up = bool(case.ready) and re.search(case.ready, text) is not None
        if todo and (up or started):
            step = todo.pop(0)
            term.type(step.text)
            term.pump(0.6)  # a slash command has to show its menu before it takes the Enter
            term.send("\r")
            pending = step.done
            started = True
            continue
        if term.proc.poll() is not None:
            return
        if case.steps:
            if started and not todo:
                return
        elif (FIRST in text and REPLY in text) or up:
            return


def run(args: argparse.Namespace) -> int:
    import pty_term as pt  # needs pyte and a Linux pty

    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    root = env.scratch("pi-resume-matrix-")
    home, work, other = root / "home", root / "work", root / "other"
    other.mkdir()
    marker = env.marker(root)
    run_env = env.base_env(root, PI_PYTHON=python, PI_USE_MOCK="1")
    cwds = {"work": work, "other": other}

    def start(argv: list[str], cwd: Path) -> pt.Term:
        return pt.Term([zypi, *argv], run_env, cwd=str(cwd))

    def finish(term: pt.Term) -> None:
        term.kill_group()
        pt.kill_marked(marker)

    try:
        # The session every case resumes (or tries to).
        term = start([], work)
        try:
            if not term.wait_for(r"Thanks for trying zypi", 25):
                print("zypi did not reach its welcome page:\n" + term.text())
                return 2
            term.type(FIRST + "\r")
            if not term.wait_for(REPLY, 25):
                print("the mock model did not answer:\n" + term.text())
                return 2
            term.type("/exit")
            term.pump(0.5)
            term.send("\r")
            term.wait_exit(15)
        finally:
            finish(term)
        made = sessions(home)
        if len(made) != 1:
            print(f"expected one session file after the first run, found {made}")
            return 2
        ids = {
            "existing": next(iter(made.values())),
            "unknown": str(uuid.uuid4()),
            "fresh": str(uuid.uuid4()),
            "idle": str(uuid.uuid4()),
        }
        print(f"session made: {ids['existing']} (first message {FIRST!r})")

        counts: Counter[str] = Counter()
        for case in CASES:
            argv = fill(case.argv, ids)
            cwd = cwds[case.cwd]
            before = sessions(home)
            if case.export:
                done = subprocess.run(
                    [zypi, *argv], env=run_env, cwd=cwd, capture_output=True, text=True, timeout=60
                )
                outcome = Outcome(
                    done.returncode,
                    done.stderr,
                    stdout=done.stdout,
                    # The export starts an agent of its own; it has to stop it before it exits.
                    left_behind=[cmd for _pid, _state, cmd in pt.processes_with_env(marker)],
                )
            else:
                term = start(argv, cwd)
                try:
                    wait_for_run(term, case, args.wait)
                    term.pump(0.6)
                    outcome = Outcome(term.proc.poll(), term.text())
                finally:
                    finish(term)
            after = sessions(home)
            outcome.new_sessions = {k: v for k, v in after.items() if k not in before}
            status = judge(case, outcome, ids, args.strict)
            counts[status] += 1
            seen = (
                f"exit {outcome.code}, replayed={outcome.replayed}, "
                f"new sessions={sorted(outcome.new_sessions.values()) or 'none'}; "
                f"{excerpt(outcome.text or outcome.stdout)}"
            )
            line = f"[{status:<5}] {case.name}"
            if status == "PASS":
                print(line, flush=True)
            elif status == "GAP":
                print(f"{line}\n         gap: {case.gap}\n         saw: {seen}", flush=True)
            elif status == "FIXED":
                print(f"{line}\n         the gap has closed; drop Case.gap: {case.gap}", flush=True)
            else:
                print(f"{line}\n         expected: {case.expect}\n         saw: {seen}", flush=True)
            if args.show:
                print("-----\n" + (outcome.text or outcome.stdout) + "\n-----")
        print(", ".join(f"{counts[s]} {s}" for s in ("PASS", "GAP", "FIXED", "FAIL")))
        return 1 if counts["FAIL"] else 0
    finally:
        pt.kill_marked(marker)
        env.remove(root)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument(
        "--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON, then this)"
    )
    ap.add_argument("--strict", action="store_true", help="known gaps fail the run too")
    ap.add_argument("--wait", type=float, default=20.0, help="seconds to give each start")
    ap.add_argument("--show", action="store_true", help="print the screen after every case")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
