"""What the flags that look for an earlier session do with a session the Python agent wrote.

    resume_matrix.py [--zypi PATH] [--python PATH] [--strict] [--wait SECONDS] [--show]

Plan 0.7 / ADR1. The pager used to read the session files of the old Rust agent to resolve
``--continue``, ``--resume <title>``, ``--session-id`` and ``zypi export``; the Python agent writes
a different layout, so each flag works on a Python session only by accident, by falling back to
the agent, or not at all. This script makes one session through the TUI (mock model, first message
"remember this"), starts zypi again with each flag and says whether it did what its help text
promises.

The cases that fail today are known gaps (``Case.gap`` says what is wrong). They are reported as
GAP and do not fail the run; ``--strict`` makes them fail. The change that lands ADR1 (the pager
asks the agent over ACP instead of reading files) has to close them and drop the ``gap`` text, and
a gap that has closed shows as FIXED until it is dropped. Exit status 0 when nothing FAILed.

Linux only (it needs a pty); everything but ``run`` is plain Python and is tested on any platform.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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

    @property
    def replayed(self) -> bool:
        """The earlier conversation is on the screen."""
        return FIRST in self.text and REPLY in self.text

    @property
    def failed(self) -> bool:
        return self.code not in (None, 0)


Ids = dict[str, str]  # existing / unknown / fresh


@dataclass(frozen=True)
class Case:
    name: str
    argv: list[str]  # `{existing}`, `{unknown}`, `{fresh}` and `{title}` are filled in
    cwd: str  # "work" (where the session was made) or "other" (a directory without sessions)
    expect: str  # what a user is promised
    holds: Callable[[Outcome, Ids], bool]
    gap: str = ""  # not empty: known not to hold today, and why
    export: bool = False  # run as a plain command, not in the TUI


CASES: list[Case] = [
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
        gap="the title is matched against the old agent's summaries only: exit 1, 'no session id "
        "or title matched'",
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
        "--resume <unknown id> names the id in an error",
        ["--resume", "{unknown}"],
        "work",
        "an error naming the id, on screen or as exit status; nothing replayed",
        lambda o, ids: (o.failed or "not found" in o.text.lower()) and not o.replayed,
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
        "a new session file whose header carries the id",
        lambda o, ids: ids["fresh"] in o.new_sessions.values(),
        gap="session/new's _meta.sessionId is ignored: the agent picks its own id",
    ),
    Case(
        "--session-id <id in use> is refused",
        ["--session-id", "{existing}"],
        "work",
        "exit 1, 'already in use', no new session",
        lambda o, ids: o.code == 1 and not o.new_sessions,
        gap="the in-use check reads the old layout, so it never sees the session: zypi starts "
        "a new one under another id",
    ),
    Case(
        "export <id> prints the transcript",
        ["export", "{existing}"],
        "work",
        "exit 0 and the conversation on stdout",
        lambda o, ids: o.code == 0 and FIRST in o.stdout,
        gap="export reads the old layout: \"Session '<id>' not found.\", exit 1",
        export=True,
    ),
    Case(
        "export <unknown id> fails without output",
        ["export", "{unknown}"],
        "work",
        "exit non-zero, nothing on stdout",
        lambda o, ids: o.failed and not o.stdout.strip(),
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
                outcome = Outcome(done.returncode, done.stderr, stdout=done.stdout)
            else:
                term = start(argv, cwd)
                try:
                    end = time.monotonic() + args.wait
                    while time.monotonic() < end:
                        term.pump(0.3)
                        if term.proc.poll() is not None or (
                            FIRST in term.text() and REPLY in term.text()
                        ):
                            break
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
