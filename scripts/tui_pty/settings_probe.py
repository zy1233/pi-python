"""Which settings on zypi's settings page change anything for the Python agent? (plan decision 14)

    settings_probe.py [--zypi PATH] [--python PATH] [--list] [--only TEXT]

The settings page came with the Rust runtime that the Python agent replaced. A setting that only
steers the pager (theme, scrolling) works as before. A setting that was meant for the old agent,
or for a prompt that the old agent offered, can change nothing now. This script shows four things:

- ``rows``: the rows the page offers today, per section (``--list`` prints only this);
- ``remember_tool_approvals``: the permission question with the setting unset, on and off. The
  Python agent offers two answers ("Allow once", "Reject") and no "Always allow ...", so the setting
  has nothing to switch on or off;
- ``default_selected_permission``: which row the cursor is on in the first question, per value.
  Only ``reject`` moves it; the other three land on "Allow once";
- ``default_model``: whether the model that the setting saves (``[models].default``) is the one
  that the next start uses (the label on the prompt box after the first turn);
- ``wire``: a config file that sets the other settings that were meant for the old agent
  (``hunk_tracker_mode``, ``fork_secondary_model``, ``prompt_suggestions``, ``auto_update``,
  the ask-question timeout) against one that does not: the ACP lines, the agent's variables and
  the LLM call are the same, so none of them reaches the agent. The agent has no
  ``ask_user_question`` tool either (``tools``).

What no probe can show is a setting that does something inside the pager. For those the plan's
table rests on the code: who reads the setting (see the plan, section 10.30).
Uses ``wire_probe.py`` and ``wire_log_agent.py``. Linux only (it needs a pty).
"""

from __future__ import annotations

import argparse
import re
import sys

import wire_probe as wp
import zypi_env as env

WRITE_CALL = {"name": "write", "arguments": {"path": "probe.txt", "content": "probe"}}
# "1 (●) Allow once": one answer of the permission question; ● marks the row the cursor is on.
ANSWER_ROW = re.compile(r"^\W*\d \((?P<mark>[●○])\) (?P<text>.+?)\s*$", re.M)
SECTION = re.compile(r"^(?P<name>[A-Z][A-Za-z &-]+?) ─{3,}$")
ROWS_HIGH = 80  # the whole page, without a scroll bar to read around
ROWS_WIDE = 140


def answer_rows(screen: str) -> list[tuple[str, bool]]:
    """The answers of a permission question: ``(text, the cursor is on it)``."""
    return [(m["text"], m["mark"] == "●") for m in ANSWER_ROW.finditer(screen)]


def settings_rows(zypi: str, python: str) -> list[tuple[str, str, str]]:
    """What ``/settings`` shows: ``(section, label, value)`` in page order."""
    import pty_term as pt

    root = env.scratch("pi-settings-")
    (root / "home" / "agent.toml").write_text(wp.AGENT_TOML, encoding="utf-8")
    run_env = env.base_env(root, PI_PYTHON=python, PI_USE_MOCK="1")
    term = pt.Term([zypi], run_env, cwd=str(root / "work"), rows=ROWS_HIGH, cols=ROWS_WIDE)
    try:
        if not term.wait_for(wp.READY, 25):
            return []
        term.pump(1.0)
        term.type("/settings")
        term.pump(0.6)
        term.send("\r")
        term.wait_for(r"/ to search", 10)
        term.pump(1.0)
        screen = term.text()
    finally:
        term.kill_group()
        pt.kill_marked(env.marker(root))
        env.remove(root)
    return parse_settings_page(screen)


def parse_settings_page(screen: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    section = ""
    inside = False
    for line in screen.splitlines():
        if "Settings ─" in line and "┌" in line:
            inside = True
            continue
        if not inside:
            continue
        if "Tip ·" in line:  # the row list ends where the page's footer starts
            break
        first, last = line.find("│"), line.rfind("│")
        if first < 0 or last <= first:
            continue
        inner = line[first + 1 : last].strip()
        head = SECTION.match(inner)
        if head:
            section = head["name"]
        elif inner.startswith("▸"):
            parts = re.split(r"\s{2,}", inner[1:].strip(), maxsplit=1)
            rows.append((section, parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""))
    return rows


def show_rows(rows: list[tuple[str, str, str]]) -> None:
    print(f"== rows: {len(rows)} on the page")
    last = None
    for section, label, value in rows:
        if section != last:
            print(f"  [{section}]")
            last = section
        print(f"    {label:<30} {value}")


def run(name: str, zypi: str, python: str, config: str = "", **options):
    return wp.execute(name, (), zypi=zypi, python=python, config=config, **options)


def check_remember(zypi: str, python: str) -> None:
    print("\n== remember_tool_approvals: the permission question, two turns, each one Enter")
    for label, config in (
        ("unset", ""),
        ("true", "[ui]\nremember_tool_approvals = true\n"),
        ("false", "[ui]\nremember_tool_approvals = false\n"),
    ):
        result = run(
            f"remember={label}",
            zypi,
            python,
            config,
            tool_call=WRITE_CALL,
            prompts=("go", "go"),
        )
        rows = [text for text, _ in answer_rows(result.question)]
        print(f"  {label:<6} questions={len(result.questions)}  answers offered: {rows}")


def check_cursor(zypi: str, python: str) -> None:
    print("\n== default_selected_permission: the row the cursor is on in the first question")
    for value in (
        None,
        "always_allow_all_sessions",
        "allow_once",
        "allow_command_always",
        "reject",
    ):
        config = "" if value is None else f'[ui]\ndefault_selected_permission = "{value}"\n'
        result = run(f"cursor={value}", zypi, python, config, tool_call=WRITE_CALL, answer=None)
        chosen = [text for text, on in answer_rows(result.question) if on]
        print(f"  {value or '(unset)':<28} cursor on: {chosen}")


def check_default_model(zypi: str, python: str) -> None:
    print("\n== default_model: [models].default in config.toml, label after the first turn")
    for label, config in (
        ("unset", ""),
        ("[models] default = mock-b", '[models]\ndefault = "mock-b"\n'),
    ):
        result = run(f"model={label}", zypi, python, config)
        used = wp.summarize(result).get("spy.model")
        print(f"  {label:<28} label after turn: {result.label_end!r}  LLM call used: {used}")


def check_wire(zypi: str, python: str) -> None:
    print("\n== wire: settings that were meant for the old agent, set against not set")
    config = (
        "[ui]\n"
        'hunk_tracker_mode = "all_dirty"\n'
        'fork_secondary_model = "mock-b"\n'
        "prompt_suggestions = false\n"
        "remember_tool_approvals = false\n"
        "\n[cli]\nauto_update = false\n"
        "\n[toolset.ask_user_question]\ntimeout_enabled = false\n"
    )
    base = wp.summarize(run("wire=base", zypi, python))
    other = wp.summarize(run("wire=set", zypi, python, config))
    diff = wp.changes(base, other)
    if diff:
        for line in diff:
            print(f"  CHANGED {line}")
    else:
        print("  NO CHANGE on the wire, in the agent's variables or in the LLM call")
    print(f"  tools the LLM is offered: {base.get('spy.tools')}")


CHECKS = {
    "remember": check_remember,
    "cursor": check_cursor,
    "model": check_default_model,
    "wire": check_wire,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--zypi", help="path to the zypi binary")
    parser.add_argument("--python", help="interpreter that has pi_agent_cli installed")
    parser.add_argument("--list", action="store_true", help="only list the rows of the page")
    parser.add_argument("--only", help="run the checks whose name contains this text")
    args = parser.parse_args()
    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    print(f"zypi: {zypi}")
    show_rows(settings_rows(zypi, python))
    if args.list:
        return 0
    for name, check in CHECKS.items():
        if args.only and args.only not in name:
            continue
        check(zypi, python)
    return 0


if __name__ == "__main__":
    sys.exit(main())
