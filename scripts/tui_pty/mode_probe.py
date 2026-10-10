"""Which of the permission modes the TUI offers does the Python agent honour?

    mode_probe.py [--zypi PATH] [--python PATH] [--only N] [--show]

Plan 0.8 / 1.P3. The TUI cycles through modes with Shift+Tab (Normal, Always-Approve) and takes
``--always-approve`` and ``--permission-mode default|bypassPermissions`` on the command line. It
used to offer Plan, Auto and the settings page's Default as well, and four more
``--permission-mode`` values; the Python agent honoured none of them (section 10.11) and 1.P3 (a)
hid them (section 10.13). While the session runs the TUI tells the agent about a change with a
``pi/yolo_mode_changed`` notification (Normal / Always-Approve); at the start it puts the mode in
the ``_meta`` of ``session/new`` (``yoloMode``, ``autoMode``), which the agent does not read. This
script puts that to the test: for each way of choosing a mode (Shift+Tab, a flag, the choice an
earlier zypi saved in ``config.toml``) it starts a fresh zypi with ``tests/_tool_agent.py``
(permission mode ``ask``, its first turn calls ``write``) and records the label the status bar
showed, whether a permission question appeared and whether the file was written without anybody
answering. The values ``--permission-mode`` no longer takes are run
too, each on its own: they must be refused at once, with the reason, instead of starting a TUI.
Last, the settings page (``/settings``): whether a search for "plan" finds a "Plan mode" row, and
which choices the Permission mode picker lists.

It judges nothing: the table is what the plan (section 10.11, appendix A) cites, and what a change
to the mode mapping has to be compared with. Linux only (it needs a pty); the table of scenarios
and the formatting are plain Python and tested on any platform.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

import zypi_env as env

SHIFT_TAB = "\x1b[Z"
AGENT = env.AGENT_TESTS / "_tool_agent.py"
# What the Python agent's question looks like on the screen (its option names are "Allow once" and
# "Reject", ``pi_agent_cli.permissions.PERMISSION_OPTIONS``).
QUESTION = re.compile(r"Allow once")
# What ``zypi --permission-mode`` takes: "default" is ask, "bypassPermissions" is always-approve.
ACCEPTED_PERMISSION_MODES = ("default", "bypassPermissions")
# The rest of the six values it used to take. The agent acts on none of them (``acceptEdits`` and
# ``dontAsk`` name modes that exist nowhere), so the flag refuses them instead of ignoring them.
REJECTED_PERMISSION_MODES = ("acceptEdits", "auto", "dontAsk", "plan")
# The choices the settings page's Permission mode picker could list, in its order. "Default" (the
# agent drops it) and "Auto" (the agent treats it as always-approve) are the two it no longer lists.
PICKER_CHOICES = ("Default", "Ask", "Auto", "Always approve")


@dataclass(frozen=True)
class Scenario:
    name: str
    keys: tuple[str, ...] = ()  # sent in order after the welcome page is up
    flags: tuple[str, ...] = ()  # zypi's command line
    config: str = ""  # what $PI_HOME/config.toml holds at the start (an earlier run's choice)


def saved(mode: str) -> Scenario:
    """A start whose config.toml says ``permission_mode = MODE``: what an earlier zypi saved."""
    return Scenario(
        f"config.toml: permission_mode = {mode}", config=f'[ui]\npermission_mode = "{mode}"\n'
    )


SCENARIOS: list[Scenario] = [
    Scenario("Normal (nothing chosen)"),
    Scenario("--always-approve", flags=("--always-approve",)),
    Scenario("Shift+Tab x1: Always-Approve", keys=(SHIFT_TAB,)),
    Scenario("Shift+Tab x2: Normal again", keys=(SHIFT_TAB,) * 2),
    # The ring used to go on to Plan and Auto here; now it comes round to Always-Approve.
    Scenario("Shift+Tab x3: Always-Approve", keys=(SHIFT_TAB,) * 3),
    *(
        Scenario(f"--permission-mode {mode}", flags=("--permission-mode", mode))
        for mode in ACCEPTED_PERMISSION_MODES
    ),
    # What an earlier zypi may have saved: Auto and Default are no longer offered, so a start
    # that finds them must come up asking (Normal); Always-Approve is still honoured.
    saved("auto"),
    saved("default"),
    saved("always-approve"),
]


@dataclass
class Observation:
    scenario: Scenario
    ready: bool = False
    label: str = ""  # the status bar's frame, e.g. "mock · plan"
    asked: bool = False  # a permission question reached the screen
    written_unasked: bool = False  # the file was there before anyone answered
    written_after_answer: bool | None = None  # None: nobody was asked


@dataclass
class Rejection:
    """What ``zypi --permission-mode MODE`` did with a value it no longer takes."""

    mode: str
    exit_code: int | None = None  # None: it was still running when the time was up
    message: str = ""  # the first line it printed on stderr


@dataclass
class SettingsOffer:
    """What the settings page puts in front of the user about modes."""

    opened: bool = False
    plan_row: bool = False  # the search "plan" lists a row called "Plan mode"
    choices: list[str] = field(default_factory=list)  # what the Permission mode picker lists


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


def picker_choices(screen: str) -> list[str]:
    """The Permission mode choices an open picker lists ("Ask · Prompt for permission ...").

    The picker's title line names "Settings" and "Permission mode" and its footer says "Enter
    select"; only the lines in between count, since whatever is behind the page can say anything.
    A screen without the picker gives an empty list.
    """
    lines = screen.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "Settings" in line and "Permission mode" in line),
        None,
    )
    if start is None:
        return []
    stop = next((i for i in range(start + 1, len(lines)) if "Enter select" in lines[i]), len(lines))
    inside = "\n".join(lines[start + 1 : stop])
    return [choice for choice in PICKER_CHOICES if f"{choice} · " in inside]


def format_settings(offer: SettingsOffer) -> str:
    if not offer.opened:
        return "settings page: did not open"
    plan = "yes" if offer.plan_row else "no"
    choices = ", ".join(offer.choices) or "(the picker did not open)"
    return (
        f"settings page: a 'Plan mode' row for the search 'plan': {plan}; picker offers: {choices}"
    )


def rejection_message(stderr: str) -> str:
    """The first non-blank line of what zypi printed: clap's ``error: invalid value ...``."""
    for line in stderr.splitlines():
        if line.strip():
            return line.strip()
    return ""


def format_rejection(rejection: Rejection) -> str:
    code = "still running" if rejection.exit_code is None else f"exit {rejection.exit_code}"
    return (
        f"--permission-mode {rejection.mode:<12} {code:<14} {rejection.message or '(no message)'}"
    )


def refuse(mode: str, zypi: str) -> Rejection:
    """Run ``zypi --permission-mode MODE``: it should refuse before it opens a terminal."""
    root = env.scratch("pi-mode-reject-")
    try:
        proc = subprocess.Popen(
            [zypi, "--permission-mode", mode],
            env=env.base_env(root),
            cwd=str(root / "work"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # It did not refuse: it started. Take the whole group down (the agent is a child).
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return Rejection(mode)
        return Rejection(mode, proc.returncode, rejection_message(err))
    finally:
        env.remove(root)


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
    if scenario.config:
        (root / "home" / "config.toml").write_text(scenario.config, encoding="utf-8")
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


def observe_settings(zypi: str, python: str, show: bool) -> SettingsOffer:
    """Open /settings on the welcome page, search "plan", open the Permission mode picker."""
    import pty_term as pt  # needs pyte and a Linux pty

    root = env.scratch("pi-mode-settings-")
    (root / "home" / "agent.toml").write_text(
        '[model]\nprovider = "mock"\nid = "mock-a"\n', encoding="utf-8"
    )
    marker = env.marker(root)
    run_env = env.base_env(root, PI_PYTHON=python, PI_USE_MOCK="1")
    offer = SettingsOffer()
    term = pt.Term([zypi], run_env, cwd=str(root / "work"))
    try:
        if not term.wait_for(r"Thanks for trying zypi", 25):
            return offer
        term.type("/settings")
        term.pump(0.6)
        term.send("\r")
        offer.opened = term.wait_for(r"/ to search", 10)
        if not offer.opened:
            return offer
        term.pump(1.0)
        term.type("/")  # starts the page's search; what follows is the query
        term.type("plan")
        term.pump(0.8)
        screen = term.text()
        offer.plan_row = "Plan mode" in screen
        if show:
            print("----- search: plan\n" + screen + "\n-----")
        term.send("\x1b")  # clears the search
        term.pump(0.4)
        term.type("/")
        term.type("permission")
        term.pump(0.6)
        term.send("\r")  # keeps the match
        term.pump(0.5)
        term.send("\r")  # opens the picker on the row
        term.pump(0.8)
        screen = term.text()
        offer.choices = picker_choices(screen)
        if show:
            print("----- the Permission mode picker\n" + screen + "\n-----")
    finally:
        term.kill_group()
        pt.kill_marked(marker)
        env.remove(root)
    return offer


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
    if args.only is None:
        for mode in REJECTED_PERMISSION_MODES:
            print(format_rejection(refuse(mode, zypi)))
        print(format_settings(observe_settings(zypi, python, args.show)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
