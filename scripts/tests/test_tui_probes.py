"""The helpers of the flag and settings probes in scripts/tui_pty/ (no zypi needed).

``flags_probe.py`` and ``settings_probe.py`` judge nothing: they print what a run showed. So what
they print is only as good as the code that reads a run (``wire_probe.summarize`` and ``changes``),
the code that reads the settings page and the permission question off a screen, and the stand-in
agent that logs the wire. These tests feed that code a made-up run and a made-up screen, and run
the stand-in agent around a stub that echoes. Nothing here starts zypi or a pty, so it runs on
Windows too.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

TUI_PTY = Path(__file__).resolve().parents[1] / "tui_pty"
# Two marks of the TUI that look like > to a linter: the prompt mark and the more chevron.
PROMPT = "\u276f"
MORE = "\u203a"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TUI_PTY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("zypi_env")  # `wire_probe` and `settings_probe` import it by name
wp = _load("wire_probe")  # the PTY driver is imported by `execute`, not at load
sp = _load("settings_probe")  # likewise: by `settings_rows`


def _line(direction: str, message: dict) -> dict:
    return {"d": direction, "t": 0.1, "line": json.dumps(message)}


def _run(**fields) -> object:
    run = wp.Run("case", root="/tmp/pi-probe-abc", root_real="/tmp/real/pi-probe-abc")
    run.ready = True
    for key, value in fields.items():
        setattr(run, key, value)
    return run


# ---- wire_probe: reading a run ---------------------------------------------------------------


def test_the_prompt_box_label_is_the_text_in_the_bottom_border():
    screen = "\n".join(
        [f"{PROMPT} go", "╭────────╮", f"│ {PROMPT}      │", "╰──── mock-a · always-approve ─╯"]
    )
    assert wp.status_label(screen) == "mock-a · always-approve"
    assert wp.status_label("no box here") == ""


def test_a_run_has_asked_when_a_question_reached_the_screen():
    run = wp.Run("case")
    assert not run.asked
    assert run.question == ""
    run.questions += ["first screen", "second screen"]
    assert run.asked
    assert run.question == "first screen"


def test_the_summary_leaves_out_what_differs_without_meaning_something():
    start = {
        "d": "start",
        "argv": [],
        "env": {"PI_HOME": "/h", "PI_USE_MOCK": "1", "GROK_DEBUG_LOG": "/tmp/real/pi-probe-abc/d"},
        "env_names": ["PATH", "PI_HOME", "PI_USE_MOCK", "WIRE_LOG", "GROK_DEBUG_LOG"],
    }
    wire = [
        start,
        _line(
            "c2a",
            {
                "method": "session/new",
                "params": {"cwd": "/tmp/real/pi-probe-abc/work", "sessionId": "x"},
            },
        ),
        # What the Python agent drops at its entry, and what holds pids and times:
        _line("c2a", {"method": "_x.ai/log", "params": {"pid": 123}}),
        _line("a2c", {"id": 3, "error": {"code": -32601, "message": "Method not found"}}),
        _line("a2c", {"id": 4, "result": {}}),
    ]
    run = _run(wire=wire, label="mock-a", label_end="mock-a")
    out = wp.summarize(run)
    # The scratch folder and ids do not stay in the values, so that two runs can be compared:
    assert out["c2a session/new[0].cwd"] == "<root>/work"
    assert not any("_x.ai" in key for key in out)
    assert out["agent.env.GROK_DEBUG_LOG"] == "<root>/d"
    # The variables of the harness are the same in every run and say nothing about a flag:
    assert "agent.env.PI_HOME" not in out
    assert "agent.env.PI_USE_MOCK" not in out
    assert out["agent.env_names"] == ["GROK_DEBUG_LOG", "PATH"]
    assert out["a2c error[0]"] == {"code": -32601, "message": "Method not found"}
    assert "a2c error[1]" not in out


def test_only_the_first_three_lines_of_one_method_are_summarized():
    wire = [_line("c2a", {"method": "session/prompt", "params": {"n": n}}) for n in range(5)]
    out = wp.summarize(_run(wire=wire))
    assert [key for key in out if key.startswith("c2a session/prompt")] == [
        "c2a session/prompt[0].n",
        "c2a session/prompt[1].n",
        "c2a session/prompt[2].n",
    ]


def test_the_summary_of_a_refused_run_is_its_first_line():
    run = _run(
        early=True,
        exit_code=2,
        screen_end="\n  error: --worktree does not work here\nmore\n",
    )
    assert wp.summarize(run) == {
        "ready": True,
        "label": "",
        "label.after_turn": "",
        "refused": "exit 2: error: --worktree does not work here",
    }


def test_the_summary_has_what_the_llm_call_would_have_been_sent():
    spy = [
        {
            "model": {"provider": "mock", "id": "mock-a", "reasoning": False},
            "reasoning": None,
            "system_prompt": "You are /tmp/real/pi-probe-abc/work",
            "tools": ["bash", "write"],
            "last_text": "go",
        }
    ]
    out = wp.summarize(_run(spy=spy, facts={"files": "probe.txt"}))
    assert out["spy.calls"] == 1
    assert out["spy.model"] == "mock/mock-a"
    assert out["spy.tools"] == ["bash", "write"]
    assert out["spy.system_prompt"] == "You are <root>/work"
    assert out["fact.files"] == "probe.txt"


# ---- wire_probe: what differs ----------------------------------------------------------------


def test_two_equal_runs_have_no_changes():
    summary = {"label": "mock-a", "spy.tools": ["bash"], "agent.env_names": ["A"]}
    assert wp.changes(summary, dict(summary)) == []


def test_a_changed_value_is_listed_with_the_old_and_the_new_one():
    base = {"label": "mock-a", "same": 1}
    other = {"label": "mock-b", "same": 1, "c2a x[0].new": True}
    assert wp.changes(base, other) == [
        "c2a x[0].new: (absent) -> true",
        "label: mock-a -> mock-b",
    ]


def test_added_and_removed_variable_names_are_listed_as_two_sets():
    base = {"agent.env_names": ["PATH", "OLD"]}
    other = {"agent.env_names": ["PATH", "NEW"]}
    assert wp.changes(base, other) == ["agent.env_names: +['NEW'] -['OLD']"]


def test_a_changed_long_text_is_shown_as_the_lines_that_differ():
    old = "\n".join(f"line {n}" for n in range(30))
    new = old + "\nALWAYS-ANSWER-IN-FRENCH"
    [line] = wp.changes({"spy.system_prompt": old}, {"spy.system_prompt": new})
    assert line.startswith("spy.system_prompt: ")
    assert "+ALWAYS-ANSWER-IN-FRENCH" in line
    assert "line 3" not in line  # only the difference, not the whole text


def test_a_refused_run_is_reported_as_refused_and_never_as_no_change():
    base = {"label": "mock-a"}
    assert wp.changes(base, {"refused": "exit 2: error: nope"}) == ["refused: exit 2: error: nope"]
    assert wp.changes({"refused": "exit 2: x"}, base) == ["the base run was refused"]


# ---- settings_probe: reading the screen ------------------------------------------------------


def _box(inner: str, width: int = 60) -> str:
    return " " * 4 + "│" + inner.ljust(width) + "│"


PAGE = "\n".join(
    [
        "  /work",
        " " * 4 + "┌─ Settings " + "─" * 40 + " [✗] ─┐",
        _box(""),
        _box("   / to search"),
        _box("  " + "─" * 56),
        _box("   Appearance " + "─" * 40),
        _box("  ▸ Compact mode" + " " * 30 + "off"),
        _box("  ▸ Default screen mode" + " " * 15 + f"Fullscreen  {MORE}"),
        _box(""),
        _box("   Editor & Input " + "─" * 36),
        _box("  ▸ Prompt suggestions" + " " * 25 + "on"),
        _box("   Agent & Approval " + "─" * 34),
        _box(f"  ▸ Default selected permission    Always allow all sessions  {MORE}"),
        _box("  ▸ Show contextual hints" + " " * 28 + MORE),
        _box("      Tip · Most settings are saved to $PI_HOME/config.toml"),
        "  ╭──────╮",
        f"  │ {PROMPT}    │",
    ]
)


def test_the_settings_page_gives_section_label_and_value_per_row():
    assert sp.parse_settings_page(PAGE) == [
        ("Appearance", "Compact mode", "off"),
        ("Appearance", "Default screen mode", f"Fullscreen  {MORE}"),
        ("Editor & Input", "Prompt suggestions", "on"),
        (
            "Agent & Approval",
            "Default selected permission",
            f"Always allow all sessions  {MORE}",
        ),
        ("Agent & Approval", "Show contextual hints", MORE),
    ]


def test_a_screen_without_the_settings_page_has_no_rows():
    assert sp.parse_settings_page(f"  {PROMPT} go\n  Hello from mock\n") == []


QUESTION = """
  ◆ Run write probe.txt
  ┃
  ┃  Allow write?
  ┃
  ┃  1 (●) Allow once
  ┃  2 (○) No, reject (type to add feedback)
  ┃
  1/2:select  │  Tab:next option
"""


def test_the_answers_of_a_permission_question_show_where_the_cursor_is():
    assert sp.answer_rows(QUESTION) == [
        ("Allow once", True),
        ("No, reject (type to add feedback)", False),
    ]
    moved = QUESTION.replace("1 (●)", "1 (○)").replace("2 (○)", "2 (●)")
    assert [text for text, on in sp.answer_rows(moved) if on] == [
        "No, reject (type to add feedback)"
    ]
    assert sp.answer_rows("no question here") == []


# ---- wire_log_agent: the stand-in that logs the wire -----------------------------------------


def test_the_wire_log_agent_copies_both_ways_and_hides_secrets(tmp_path):
    stub = tmp_path / "upper_agent.py"
    stub.write_text(
        "import sys\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(line.upper())\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    log = tmp_path / "wire.jsonl"
    env = {
        **os.environ,
        "WIRE_LOG": str(log),
        "WIRE_AGENT_SCRIPT": str(stub),
        "PI_VISIBLE": "yes",
        "PI_API_KEY": "must-not-be-logged",
    }
    done = subprocess.run(
        [sys.executable, str(TUI_PTY / "wire_log_agent.py"), "--some-arg"],
        input=b'{"a": 1}\n{"b": 2}\n',
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    # Nothing changes for either side: the client gets what the agent wrote, the agent what the
    # client sent.
    assert done.stdout.replace(b"\r\n", b"\n") == b'{"A": 1}\n{"B": 2}\n'
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    start = records[0]
    assert start["d"] == "start"
    assert start["argv"] == ["--some-arg"]
    assert start["env"]["PI_VISIBLE"] == "yes"
    assert "PI_API_KEY" not in start["env"]  # the value of a secret is never written
    assert "must-not-be-logged" not in log.read_text(encoding="utf-8")
    assert "PI_API_KEY" in start["env_names"]  # its name is
    sent = [r["line"] for r in records if r["d"] == "c2a"]
    got = [r["line"] for r in records if r["d"] == "a2c"]
    assert sent == ['{"a": 1}', '{"b": 2}']
    assert got == ['{"A": 1}', '{"B": 2}']


def test_the_flags_probe_has_a_case_for_every_flag_once():
    flags_probe = _load("flags_probe")
    names = [case.name for case in flags_probe.cases()]
    assert len(names) > 30
    assert len(set(names)) == len(names)
    # A case is compared with a base run that exists:
    assert {case.base for case in flags_probe.cases()} <= set(flags_probe.BASES)
