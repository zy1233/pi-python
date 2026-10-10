"""Which of the permission modes the TUI offers does the Python agent honour?

    mode_probe.py [--zypi PATH] [--python PATH] [--only N] [--show]

Plan 0.8 / 1.P3. The TUI cycles through modes with Shift+Tab (Normal, Always-Approve, Normal,
Plan, Auto) and takes ``--always-approve`` and ``--permission-mode <mode>`` on the command line.
While the session runs it tells the agent about a change with a ``pi/yolo_mode_changed``
notification (Normal / Always-Approve / Auto) and, for Plan, a ``session/set_mode`` request; the
Python agent understands the notification only, answers the request with "method not found", and
the TUI only logs that. At the start it puts the mode in the ``_meta`` of ``session/new``
(``yoloMode``, ``autoMode``), which the agent does not read. This script puts that to the test:
for each way of choosing a mode it starts a fresh zypi with ``tests/_tool_agent.py`` (permission
mode ``ask``, its first turn calls ``write``) and records the label the status bar showed, whether
a permission question appeared and whether the file was written without anybody answering.

It judges nothing: the table is what the plan (section 10.11, appendix A) cites, and what a change
to the mode mapping has to be compared with. Linux only (it needs a pty); the table of scenarios
and the formatting are plain Python and tested on any platform.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass

import zypi_env as env

SHIFT_TAB = "\x1b[Z"
AGENT = env.AGENT_TESTS / "_tool_agent.py"
# What the Python agent's question looks like on the screen (its option names are "Allow once" and
# "Reject", ``pi_agent_cli.permissions.PERMISSION_OPTIONS``).
QUESTION = re.compile(r"Allow once")
# The values ``zypi --help`` lists for ``--permission-mode``.
PERMISSION_MODE_VALUES = ("default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan")


@dataclass(frozen=True)
class Scenario:
    name: str
    keys: tuple[str, ...] = ()  # sent in order after the welcome page is up
    flags: tuple[str, ...] = ()  # zypi's command line


SCENARIOS: list[Scenario] = [
    Scenario("Normal (nothing chosen)"),
    Scenario("--always-approve", flags=("--always-approve",)),
    Scenario("Shift+Tab x1: Always-Approve", keys=(SHIFT_TAB,)),
    Scenario("Shift+Tab x2: Normal again", keys=(SHIFT_TAB,) * 2),
    Scenario("Shift+Tab x3: Plan", keys=(SHIFT_TAB,) * 3),
    Scenario("Shift+Tab x4: Auto", keys=(SHIFT_TAB,) * 4),
    *(
        Scenario(f"--permission-mode {mode}", flags=("--permission-mode", mode))
        for mode in PERMISSION_MODE_VALUES
    ),
]


@dataclass
class Observation:
    scenario: Scenario
    ready: bool = False
    label: str = ""  # the status bar's frame, e.g. "mock · plan"
    asked: bool = False  # a permission question reached the screen
    written_unasked: bool = False  # the file was there before anyone answered
    written_after_answer: bool | None = None  # None: nobody was asked


def status_label(screen: str) -> str:
    """The text in the frame of the prompt box's bottom border, where the mode is shown."""
    for line in reversed(screen.splitlines()):
        text = line.strip()
        if text.startswith("╰") and text.endswith("╯"):
            return text.strip("╰╯─ ")
    return ""


def format_row(obs: Observation) -> str:
    answered = {None: "-", True: "yes", False: "NO"}[obs.written_after_answer]
    return (
        f"{obs.scenario.name:36} label={obs.label or '(none)':26} "
        f"asked={'yes' if obs.asked else 'no':3}  written without an answer="
        f"{'yes' if obs.written_unasked else 'no':3}  written after an answer={answered}"
    )


def observe(scenario: Scenario, zypi: str, python: str, show: bool) -> Observation:
    import pty_term as pt  # needs pyte and a Linux pty

    root = env.scratch("pi-mode-probe-")
    marker = env.marker(root)
    target = root / "work" / "probe.txt"
    call = {"name": "write", "arguments": {"path": str(target), "content": "probe"}}
    run_env = env.base_env(
        root,
        PI_AGENT_COMMAND=f"{python} {AGENT}",
        PI_TEST_PERMISSION="ask",
        PI_TEST_TOOL_CALL=json.dumps(call),
    )
    obs = Observation(scenario)
    term = pt.Term([zypi, *scenario.flags], run_env, cwd=str(root / "work"))
    try:
        obs.ready = term.wait_for(r"Thanks for trying zypi", 25)
        for key in scenario.keys:
            term.send(key)
            term.pump(1.0)
        obs.label = status_label(term.text())
        term.type("go\r")
        screen = ""
        end = time.monotonic() + 12
        while time.monotonic() < end and not (obs.asked or obs.written_unasked):
            term.pump(0.3)
            screen = term.text()
            obs.asked = bool(QUESTION.search(screen))
            obs.written_unasked = target.exists()
        if obs.asked:
            if show:
                print("----- the question\n" + screen + "\n-----")
            term.send("\r")  # the default option: allow once
            term.pump(2.0)
            obs.written_after_answer = target.exists()
    finally:
        term.kill_group()
        pt.kill_marked(marker)
        env.remove(root)
    return obs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument("--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON)")
    ap.add_argument("--only", type=int, help="run only scenario N (0-based)")
    ap.add_argument("--show", action="store_true", help="print the screen with the question")
    args = ap.parse_args()
    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    chosen = SCENARIOS if args.only is None else [SCENARIOS[args.only]]
    for scenario in chosen:
        obs = observe(scenario, zypi, python, args.show)
        print(format_row(obs) if obs.ready else f"{scenario.name}: zypi never became ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
