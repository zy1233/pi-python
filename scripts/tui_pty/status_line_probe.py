"""Does the status line show the session with the Python agent? A probe for plan C.1 / C.2.

    status_line_probe.py [--zypi PATH] [--python PATH] [--show]

zypi can show a row of session facts (directory, model, ...) under the prompt. The Python agent
sends no status of its own, so zypi builds the row from what it knows itself: the directory, the
session id, the model and the session name (decision 15). An agent that does send a status
(``PiSessionUpdate::SessionStatus``, carried by the extension notification
``pi/session_notification``) is read first, and the client's facts only fill what it left out.
This script tests both. For each way of setting the row up (``type = "command"`` and
``type = "builtin"`` in ``$PI_HOME/config.toml``) it starts a fresh zypi twice and plays one turn:

* with the plain Python agent (mock LLM): the row must be drawn, with the model of the session,
  and a command script must be run with a payload that names that model, the session, and the
  directory. A number the client cannot know (a context percentage, a cost) must be absent from
  the payload, not zero;
* with ``status_proxy_agent.py``, the same agent plus a status message of its own: the row must
  show the model that message names, not the one the client knows. This is the control for the
  precedence, and it shows that the pager still reads the message.

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
# The model of the session (``[model] id`` in the agent.toml below), which the client knows.
AGENT_MODEL = "mock-a"
# The model the proxy's status message names (``status_proxy_agent.MODEL``, which a test pins).
PROXY_MODEL = "proxy-model"
# The scratch directory the run starts in, so the last part of the directory the row shows.
WORKDIR = "work"


@dataclass(frozen=True)
class Scenario:
    kind: str  # "command" or "builtin": a key of SECTIONS
    injected: bool  # the agent is status_proxy_agent.py, which sends a status message

    @property
    def name(self) -> str:
        return f"type = {self.kind}, " + (
            "with a status from the agent" if self.injected else "plain Python agent"
        )

    @property
    def model(self) -> str:
        """The model the row must name: the agent's status wins over what the client knows."""
        return PROXY_MODEL if self.injected else AGENT_MODEL

    @property
    def row(self) -> str:
        """A regex for what the row shows on the screen when it is drawn."""
        if self.kind == "command":
            return re.escape(ROW_TEXT)
        # The built-in row joins its items with a bar: the directory name, then the model name.
        # The context item stays out of the plain agent's row, which has no percentage to show.
        return rf"{WORKDIR}\s+│\s+{re.escape(self.model)}"


SCENARIOS = [Scenario(kind, injected) for kind in SECTIONS for injected in (False, True)]


def config_text(scenario: Scenario, script: Path) -> str:
    """``$PI_HOME/config.toml`` of a scenario: the pager's own file, with the status line only."""
    # A JSON string is a valid TOML basic string, so a path with a backslash or a quote survives.
    return "[ui.status_line]\n" + SECTIONS[scenario.kind].format(script=json.dumps(str(script)))


def payload_of(text: str) -> dict:
    """The JSON object a command script was given ({} when it is not one)."""
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def payload_fields(text: str) -> list[str]:
    """The top-level field names of the JSON a command script was given ([] when it is not JSON)."""
    return sorted(payload_of(text))


def _section(payload: dict, name: str) -> dict:
    value = payload.get(name)
    return value if isinstance(value, dict) else {}


def payload_problems(scenario: Scenario, payload: dict) -> list[str]:
    """What is wrong with the payload of a command run, one line each (none: it is right).

    Every run must name the session and the model the row shows. A run without a status from the
    agent must also give no number the client cannot know: they are absent, not zero.
    """
    problems = []
    if _section(payload, "model").get("display_name") != scenario.model:
        problems.append(f"model.display_name is not {scenario.model!r}")
    if not payload.get("session_id"):
        problems.append("session_id is missing")
    if not str(payload.get("cwd", "")).endswith(WORKDIR):
        problems.append(f"cwd does not end in {WORKDIR!r}")
    if not _section(payload, "workspace").get("current_dir"):
        problems.append("workspace.current_dir is missing")
    if not payload.get("version"):
        problems.append("version is missing")
    if payload.get("schema_version") is None:
        problems.append("schema_version is missing")
    if not scenario.injected:
        window = _section(payload, "context_window")
        if {"used_percentage", "context_tokens", "context_window_size"} & set(window):
            problems.append("context_window has a number the client cannot know")
        if "total_cost_usd" in _section(payload, "cost"):
            problems.append("cost.total_cost_usd is there, and nobody priced anything")
    return problems


def verdict(
    scenario: Scenario, *, replied: bool, ran: bool, shown: bool, problems: list[str] | None = None
) -> tuple[bool, str]:
    """Whether a run is what the guide page says: (ok, what the page says about it)."""
    if scenario.injected:
        says = "the row shows the status the agent sent, not what the client knows"
    else:
        says = "the row is drawn from what the client knows, with no status from the agent"
    # A command row needs its script to have run, with a payload that has no problem.
    ok = shown and (scenario.kind != "command" or (ran and not problems))
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
            f'[model]\nprovider = "mock"\nid = "{AGENT_MODEL}"\n', encoding="utf-8"
        )
        (home / "config.toml").write_text(config_text(scenario, script), encoding="utf-8")
        extra = {"PI_PYTHON": python, "PI_USE_MOCK": "1", "PAYLOAD_FILE": str(payload)}
        if scenario.injected:
            extra["PI_AGENT_COMMAND"] = f"{shlex.quote(python)} {shlex.quote(str(PROXY))}"
        term = pt.Term([zypi], env.base_env(root, **extra), cwd=str(root / WORKDIR))
        try:
            term.wait_for(r"Thanks for trying zypi", 20)
            term.type("hello\r")
            replied = term.wait_for(r"Hello from mock", 20)
            term.pump(3.0)  # past the 300 ms debounce, and the script's own run time
            if args.show:
                print(f"----- {scenario.name}\n{term.text()}\n-----")
            ran = payload.exists()
            text = payload.read_text(encoding="utf-8") if ran else ""
            fields = payload_fields(text)
            problems = payload_problems(scenario, payload_of(text)) if ran else []
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
                for problem in problems:
                    print(f"  payload problem:              {problem}")
        print(f"  the row is on the screen:     {'yes' if shown else 'no'}")
        ok, says = verdict(scenario, replied=replied, ran=ran, shown=shown, problems=problems)
        failed += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {scenario.name}: {says}")
    print(f"\n{len(SCENARIOS) - failed}/{len(SCENARIOS)} runs are what the page says")
    return 1 if failed else 0


if __name__ == "__main__":
    t0 = time.monotonic()
    rc = main()
    print(f"({time.monotonic() - t0:.0f}s)")
    sys.exit(rc)
