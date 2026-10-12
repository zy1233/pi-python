"""Does the status line show anything with the Python agent? A probe for plan C.1 / C.2.

    status_line_probe.py [--zypi PATH] [--python PATH] [--show]

zypi can show a row of session facts (model, context use, cost) under the prompt. The pager
builds it only from a session-status update (``PiSessionUpdate::SessionStatus``, carried by the
extension notification ``pi/session_notification``) that the old Rust agent sent. The Python
agent has no code that sends it, so the guide page ``25-status-line.md`` says the row stays
empty. This script puts both halves of that to the test. For each way of setting the row up
(``type = "command"`` and ``type = "builtin"`` in ``$PI_HOME/config.toml``) it starts a fresh
zypi twice and plays one turn:

* with the plain Python agent (mock LLM): the row must not be drawn, and a command script must
  not be run;
* with ``status_proxy_agent.py``, the same agent plus the one message it never sends: the row
  must be drawn. This is the control: without it an empty row could be a mistake in the config
  text instead of a missing message.

It waits past the pager's 300 ms debounce, then records whether the script was run (and which
fields its payload held) and whether the row's text is on the screen. Linux only (it needs a
pty); the scenarios and the config text are plain Python, tested on any platform.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import zypi_env as env

PROXY = Path(__file__).resolve().parent / "status_proxy_agent.py"
# The command row's script: it keeps what zypi sends on stdin and prints a row that is easy to find.
ROW_TEXT = "STATUSROW-OK"
SCRIPT = f'#!/bin/sh\ncat > "$PAYLOAD_FILE"\necho "{ROW_TEXT}"\n'
SECTIONS = {
    "command": 'type = "command"\ncommand = {script}\n',  # {script} is a quoted TOML string
    "builtin": 'type = "builtin"\nitems = ["cwd", "model", "context"]\n',
}


@dataclass(frozen=True)
class Scenario:
    kind: str  # "command" or "builtin": a key of SECTIONS
    injected: bool  # the agent is status_proxy_agent.py, which sends the status message

    @property
    def name(self) -> str:
        return f"type = {self.kind}, " + (
            "with the status message" if self.injected else "plain Python agent"
        )

    @property
    def row(self) -> str:
        """A regex for what the row shows on the screen when it is drawn."""
        if self.kind == "command":
            return re.escape(ROW_TEXT)
        # The built-in row joins its items with a bars: the directory name, then the model name
        # (the proxy's). Without a message the model is unknown, so look for the directory alone.
        return r"work\s+│\s+proxy-model" if self.injected else r"work\s+│"


SCENARIOS = [Scenario(kind, injected) for kind in SECTIONS for injected in (False, True)]


def config_text(scenario: Scenario, script: Path) -> str:
    """``$PI_HOME/config.toml`` of a scenario: the pager's own file, with the status line only."""
    # A JSON string is a valid TOML basic string, so a path with a backslash or a quote survives.
    return "[ui.status_line]\n" + SECTIONS[scenario.kind].format(script=json.dumps(str(script)))


def payload_fields(text: str) -> list[str]:
    """The top-level field names of the JSON a command script was given ([] when it is not JSON)."""
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return sorted(value) if isinstance(value, dict) else []


def verdict(scenario: Scenario, *, replied: bool, ran: bool, shown: bool) -> tuple[bool, str]:
    """Whether a run is what the guide page says: (ok, what the page says about it)."""
    if scenario.injected:
        says = "the row is drawn once the agent sends the status message"
        ok = shown and (ran or scenario.kind != "command")
    else:
        says = "the row stays empty with the Python agent"
        ok = not shown and not ran
    return replied and ok, says


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument(
        "--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON, then this)"
    )
    ap.add_argument("--show", action="store_true", help="print the screen after the turn")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        raise SystemExit("status_line_probe.py needs Linux (a pty)")

    import pty_term as pt  # imported late: it needs pyte and a Linux pty

    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    failed = 0
    for scenario in SCENARIOS:
        root = env.scratch("pi-statusline-")
        home, user = root / "home", root / "user"
        script, payload = user / "statusline.sh", user / "payload.json"
        script.write_text(SCRIPT, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (home / "agent.toml").write_text(
            '[model]\nprovider = "mock"\nid = "mock-a"\n', encoding="utf-8"
        )
        (home / "config.toml").write_text(config_text(scenario, script), encoding="utf-8")
        extra = {"PI_PYTHON": python, "PI_USE_MOCK": "1", "PAYLOAD_FILE": str(payload)}
        if scenario.injected:
            extra["PI_AGENT_COMMAND"] = f"{shlex.quote(python)} {shlex.quote(str(PROXY))}"
        term = pt.Term([zypi], env.base_env(root, **extra), cwd=str(root / "work"))
        try:
            term.wait_for(r"Thanks for trying zypi", 20)
            term.type("hello\r")
            replied = term.wait_for(r"Hello from mock", 20)
            term.pump(3.0)  # past the 300 ms debounce, and the script's own run time
            if args.show:
                print(f"----- {scenario.name}\n{term.text()}\n-----")
            ran = payload.exists()
            fields = payload_fields(payload.read_text(encoding="utf-8")) if ran else []
            shown = bool(re.search(scenario.row, term.text()))
            term.type("/exit")
            term.pump(0.5)
            term.send("\r")
            term.wait_exit(15)
        finally:
            term.kill_group()
            pt.kill_marked(env.marker(root))
            env.remove(root)
        print(f"[{scenario.name}]")
        print(f"  the turn was answered:        {'yes' if replied else 'NO'}")
        if scenario.kind == "command":
            print(f"  the command script was run:   {'yes' if ran else 'no'}")
            if ran:
                print(f"  payload fields:               {', '.join(fields) or '(not JSON)'}")
        print(f"  the row is on the screen:     {'yes' if shown else 'no'}")
        ok, says = verdict(scenario, replied=replied, ran=ran, shown=shown)
        failed += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {scenario.name}: {says}")
    print(f"\n{len(SCENARIOS) - failed}/{len(SCENARIOS)} runs are what the page says")
    return 1 if failed else 0


if __name__ == "__main__":
    t0 = time.monotonic()
    rc = main()
    print(f"({time.monotonic() - t0:.0f}s)")
    sys.exit(rc)
