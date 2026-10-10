"""The PTY harness in scripts/tui_pty/ itself (no zypi needed).

The drivers there are only as good as the terminal and the process helpers under them, and they
run by hand on a machine with a built zypi, so these tests run bash and python under the same
`Term`. The `Term` ones need Linux (a pty, ``/proc``) and pyte and are skipped without them.
"""

from __future__ import annotations

import importlib.util
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

TUI_PTY = Path(__file__).resolve().parents[1] / "tui_pty"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TUI_PTY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


zenv = _load("zypi_env")  # stdlib only, so it loads everywhere
rmx = _load("resume_matrix")  # plain Python until `run`, which is what imports the PTY driver
mp = _load("mode_probe")  # likewise: the PTY driver is imported by `observe`


@pytest.fixture(scope="module")
def pt():
    pytest.importorskip("pyte")
    if not sys.platform.startswith("linux"):
        pytest.skip("the PTY driver needs a Linux pty and /proc")
    return _load("pty_term")


# ---- zypi_env: what a run is given ------------------------------------------------------


def test_a_run_gets_none_of_the_callers_environment(tmp_path, monkeypatch):
    # A profile that exports these must not be able to point a run at a real home.
    monkeypatch.setenv("PI_HOME", "/somewhere/real")
    monkeypatch.setenv("PI_AGENT_COMMAND", "evil")
    monkeypatch.setenv("NO_COLOR", "1")
    run_env = zenv.base_env(tmp_path, PI_USE_MOCK="1")
    assert run_env["PI_HOME"] == str(tmp_path / "home")
    assert run_env["HOME"] == str(tmp_path / "user")
    assert run_env["PI_USE_MOCK"] == "1"
    assert "PI_AGENT_COMMAND" not in run_env
    assert "NO_COLOR" not in run_env
    assert zenv.marker(tmp_path) == f"PI_HOME={tmp_path / 'home'}"


def test_scratch_makes_home_user_and_work_and_remove_deletes_them():
    root = zenv.scratch("pi-test-")
    try:
        assert sorted(p.name for p in root.iterdir()) == ["home", "user", "work"]
    finally:
        zenv.remove(root)
    assert not root.exists()


def test_find_zypi_prefers_the_argument_then_the_environment(tmp_path, monkeypatch):
    first, second = tmp_path / "first-zypi", tmp_path / "second-zypi"
    for binary in (first, second):
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
    monkeypatch.setenv("ZYPI", str(second))
    assert zenv.find_zypi(str(first)) == str(first.resolve())
    assert zenv.find_zypi(None) == str(second.resolve())


def test_find_zypi_says_how_to_get_one_when_there_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ZYPI", raising=False)
    monkeypatch.setattr(zenv, "REPO", tmp_path)  # no tui/target/*/zypi under it
    with pytest.raises(SystemExit, match="--zypi"):
        zenv.find_zypi(str(tmp_path / "missing"))


def test_agent_python_falls_back_to_the_running_interpreter(monkeypatch):
    monkeypatch.delenv("PI_PYTHON", raising=False)
    assert zenv.agent_python(None) == sys.executable
    monkeypatch.setenv("PI_PYTHON", "/opt/py")
    assert zenv.agent_python(None) == "/opt/py"
    assert zenv.agent_python("/explicit/py") == "/explicit/py"


# ---- pty_term: the terminal and the process helpers -------------------------------------


def test_term_renders_what_the_program_draws(pt, tmp_path):
    term = pt.Term(["bash", "-c", "printf 'hello\\nworld\\n'; sleep 0.2"], zenv.base_env(tmp_path))
    try:
        assert term.wait_for(r"world", 10)
        assert "hello" in term.text()
        assert term.wait_exit(5) == 0
    finally:
        term.kill_group()


def test_term_answers_cursor_position_queries(pt, tmp_path):
    # zypi asks the terminal where the cursor is; a driver that stays silent hangs it.
    child = (
        "import sys, tty\n"
        "tty.setcbreak(0)\n"
        "sys.stdout.write('\\x1b[6n')\n"
        "sys.stdout.flush()\n"
        "data = b''\n"
        "while not data.endswith(b'R'):\n"
        "    data += sys.stdin.buffer.read(1)\n"
        "print('REPLY', data[2:-1].decode())\n"
    )
    term = pt.Term([sys.executable, "-c", child], zenv.base_env(tmp_path))
    try:
        assert term.wait_for(r"REPLY 1;1", 10), term.text()
    finally:
        term.kill_group()


def test_process_helpers_find_descendants_by_environment_and_kill_group_clears_them(pt, tmp_path):
    token = f"PI_TEST_MARK={tmp_path.name}"
    term = pt.Term(
        ["bash", "-c", "sleep 30 & wait"], zenv.base_env(tmp_path, PI_TEST_MARK=tmp_path.name)
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not pt.descendants(term.proc.pid):
            time.sleep(0.05)
        kids = pt.descendants(term.proc.pid)
        assert [pt.cmdline(pid) for pid in kids] == ["sleep 30"]
        found = {pid for pid, _state, _cmd in pt.processes_with_env(token)}
        assert found == {term.proc.pid, *kids}
        assert all(pt.alive(pid) for pid in found)
        session = pt.pgrp_sid(kids[0])
        assert session is not None
        assert session == pt.pgrp_sid(term.proc.pid)  # the terminal's leader and its child
    finally:
        term.kill_group()
    assert pt.processes_with_env(token) == []


def test_closing_the_terminal_hangs_up_its_session_leader(pt, tmp_path):
    term = pt.Term(["bash", "-c", "sleep 30"], zenv.base_env(tmp_path))
    try:
        term.pump(0.3)  # let bash exec sleep, so the leader is the process that gets the hang-up
        term.close_master()
        assert term.wait_exit(5) == -signal.SIGHUP
    finally:
        term.kill_group()


def test_a_zombie_does_not_count_as_alive(pt):
    child = subprocess.Popen(["true"])  # exits at once and stays a zombie until it is waited for
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and pt.proc_state(child.pid) != "Z":
            time.sleep(0.02)
        assert pt.proc_state(child.pid) == "Z"
        assert not pt.alive(child.pid)
    finally:
        child.wait()


# ---- resume_matrix: how a run is judged -------------------------------------------------

IDS = {"existing": "e", "unknown": "u", "fresh": "f"}
REPLAYED = rmx.Outcome(None, f"{rmx.FIRST}\n{rmx.REPLY}")
FAILED = rmx.Outcome(1, "Error: no such session")


def _case(holds, gap=""):
    return rmx.Case("c", ["--continue"], "work", "something", holds, gap=gap)


def test_a_case_that_holds_passes_and_one_that_does_not_fails():
    case = _case(lambda o, ids: o.replayed)
    assert rmx.judge(case, REPLAYED, IDS) == "PASS"
    assert rmx.judge(case, FAILED, IDS) == "FAIL"


def test_a_known_gap_is_reported_and_only_strict_fails_it():
    case = _case(lambda o, ids: o.replayed, gap="the pager reads the old layout")
    assert rmx.judge(case, FAILED, IDS) == "GAP"
    assert rmx.judge(case, FAILED, IDS, strict=True) == "FAIL"


def test_a_gap_that_has_closed_is_called_out_even_when_strict():
    case = _case(lambda o, ids: o.replayed, gap="stale")
    assert rmx.judge(case, REPLAYED, IDS) == "FIXED"
    assert rmx.judge(case, REPLAYED, IDS, strict=True) == "FIXED"


def test_the_cases_are_well_formed():
    names = [case.name for case in rmx.CASES]
    assert len(names) == len(set(names))
    for case in rmx.CASES:
        assert case.cwd in ("work", "other"), case.name
        filled = rmx.fill(case.argv, {"existing": "E", "unknown": "U", "fresh": "F"})
        assert all("{" not in part for part in filled), case.name
    # Every flag ADR1 moves from files to the agent has a case. (A known gap is a statement about
    # today and goes when it closes, so no test pins which cases have one.)
    flags = {case.argv[0] for case in rmx.CASES}
    assert {"--continue", "--resume", "--session-id", "export"} <= flags


def test_the_session_id_cases_tell_a_used_id_from_a_fresh_one():
    fresh = next(case for case in rmx.CASES if "{fresh}" in case.argv)
    taken = next(case for case in rmx.CASES if case.argv == ["--session-id", "{existing}"])
    started_as_asked = rmx.Outcome(None, "", new_sessions={"x.jsonl": "f"})
    started_as_another = rmx.Outcome(None, "", new_sessions={"x.jsonl": "other"})
    refused = rmx.Outcome(1, "Error: Session ID e is already in use.")
    assert fresh.holds(started_as_asked, IDS)
    assert not fresh.holds(started_as_another, IDS)
    assert taken.holds(refused, IDS)
    assert not taken.holds(started_as_another, IDS)


def test_sessions_reads_the_id_from_each_header(tmp_path):
    folder = tmp_path / "sessions"
    folder.mkdir()
    (folder / "a.jsonl").write_text('{"type": "session", "id": "abc"}\n{"x": 1}\n', "utf-8")
    (folder / "b.jsonl").write_text("", "utf-8")
    (folder / "c.jsonl").write_text("not json\n", "utf-8")
    assert rmx.sessions(tmp_path) == {
        "a.jsonl": "abc",
        "b.jsonl": "<unreadable>",
        "c.jsonl": "<unreadable>",
    }
    assert rmx.sessions(tmp_path / "nowhere") == {}


# ---- mode_probe: which modes the Python agent honours ----------------------------------


def test_the_mode_scenarios_are_well_formed():
    names = [scenario.name for scenario in mp.SCENARIOS]
    assert len(names) == len(set(names))
    # Every stop of the Shift+Tab ring (Normal, Always-Approve) and one more lap, and the
    # command-line flag, are probed. There is no Plan or Auto stop to press for any more.
    pressed = {len(scenario.keys) for scenario in mp.SCENARIOS}
    assert {0, 1, 2, 3} <= pressed
    assert not {4, 5} & pressed
    assert any("--always-approve" in scenario.flags for scenario in mp.SCENARIOS)
    assert all(set(scenario.keys) <= {mp.SHIFT_TAB} for scenario in mp.SCENARIOS)
    # Every value `zypi --permission-mode` takes is probed, each as its own command line.
    by_flag = {
        scenario.flags[1]: scenario.flags
        for scenario in mp.SCENARIOS
        if scenario.flags[:1] == ("--permission-mode",)
    }
    assert set(by_flag) == {"default", "bypassPermissions"}
    assert all(flags == ("--permission-mode", mode) for mode, flags in by_flag.items())
    # So are the modes an earlier zypi saved in config.toml (Auto and Default must come up asking).
    saved_modes = {
        tomllib.loads(scenario.config)["ui"]["permission_mode"]
        for scenario in mp.SCENARIOS
        if scenario.config
    }
    assert saved_modes == {"auto", "default", "always-approve"}
    assert all(not scenario.config for scenario in mp.SCENARIOS if scenario.flags or scenario.keys)


def test_the_values_the_flag_refuses_are_the_rest_of_the_six_it_used_to_take():
    # `zypi --help` used to list these six; two work, four are refused and probed as refusals.
    old_values = {"default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"}
    assert set(mp.ACCEPTED_PERMISSION_MODES) == {"default", "bypassPermissions"}
    assert set(mp.REJECTED_PERMISSION_MODES) == {"acceptEdits", "auto", "dontAsk", "plan"}
    assert set(mp.ACCEPTED_PERMISSION_MODES) | set(mp.REJECTED_PERMISSION_MODES) == old_values
    assert not set(mp.ACCEPTED_PERMISSION_MODES) & set(mp.REJECTED_PERMISSION_MODES)


# The Permission mode picker as zypi draws it (pyte's display, borders kept short): the top border
# names "Settings" and "Permission mode", each choice is "Label · description", and the footer says
# "Enter select". The title's separator is U+203A, written as an escape (ruff flags the glyph).
TWO_CHOICES = [
    ("Ask", "Prompt for permission before tool actions."),
    ("Always approve", "Auto-approve every tool action. Skips ALL permission prompts."),
]
FOUR_CHOICES = [
    ("Default", "Use the agent's default permission behavior (currently equivalent to Ask)."),
    TWO_CHOICES[0],
    ("Auto", "LLM classifier approves safe tools; dangerous actions may still prompt or deny."),
    TWO_CHOICES[1],
]


def picker_screen(choices: list[tuple[str, str]]) -> str:
    def row(text: str = "") -> str:
        return "                  │" + text.ljust(70) + "│"

    lines = [
        "                  ┌─ Settings \u203a Permission mode " + "─" * 40 + " [x] ─┐",
        row("  Permission mode"),
        row("  Ask prompts for each tool action; Always approve grants all permissions"),
        row(),
        *(row(f"   ○  {label} · {text}") for label, text in choices),
        row(),
        row("      ↑/↓ nav  |  Enter select  |  Esc cancel  |  d reset"),
    ]
    return "\n".join(lines) + "\n"


PICKER_OF_TWO = picker_screen(TWO_CHOICES)
PICKER_OF_FOUR = picker_screen(FOUR_CHOICES)


def test_the_permission_picker_is_read_from_the_screen():
    assert mp.picker_choices(PICKER_OF_TWO) == ["Ask", "Always approve"]
    # The page as it was before 1.P3 (a): four choices, in the page's order.
    assert mp.picker_choices(PICKER_OF_FOUR) == ["Default", "Ask", "Auto", "Always approve"]
    # No picker on the screen (the page is still the list, or nothing is open): nothing to read.
    assert mp.picker_choices("") == []
    assert mp.picker_choices("  Permission mode        Ask\n  Remember tool approvals   on") == []
    # What is behind the page, or after the footer, is not a choice.
    behind = "  Auto · mock-a\n" + PICKER_OF_TWO + "  Default · after the footer\n"
    assert mp.picker_choices(behind) == ["Ask", "Always approve"]


def test_what_the_settings_page_offers_is_one_line():
    shown = mp.format_settings(mp.SettingsOffer(True, False, ["Ask", "Always approve"]))
    assert shown == (
        "settings page: a 'Plan mode' row for the search 'plan': no; "
        "picker offers: Ask, Always approve"
    )
    assert "row for the search 'plan': yes" in mp.format_settings(mp.SettingsOffer(True, True, []))
    assert "(the picker did not open)" in mp.format_settings(mp.SettingsOffer(True, False, []))
    assert mp.format_settings(mp.SettingsOffer()) == "settings page: did not open"
    # Every choice a picker can list is one the parser looks for.
    assert mp.PICKER_CHOICES == ("Default", "Ask", "Auto", "Always approve")


def test_a_refusal_is_reported_by_its_first_line_and_exit_code():
    stderr = (
        "\n"
        "error: invalid value 'plan' for '--permission-mode <MODE>': the agent does not act on "
        "`plan`, so the flag would change nothing (valid values: default, bypassPermissions)\n"
        "\n"
        "For more information, try '--help'.\n"
    )
    message = mp.rejection_message(stderr)
    assert message.startswith("error: invalid value 'plan' for '--permission-mode <MODE>'")
    assert "valid values: default, bypassPermissions" in message
    assert mp.rejection_message("") == ""
    assert mp.rejection_message("  \n \n") == ""

    refused = mp.format_rejection(mp.Rejection("plan", 2, message))
    assert refused.startswith("--permission-mode plan")
    assert "exit 2" in refused and message in refused
    # A zypi that did not refuse is a result too: it started up and was killed.
    started = mp.format_rejection(mp.Rejection("auto"))
    assert "still running" in started and "(no message)" in started


def test_the_status_label_is_read_from_the_frame_of_the_prompt_box():
    screen = "  ╭────────────╮\n  │ hello      │\n  ╰─ mock · plan ─╯\n\n  Shift+Tab:mode\n"
    assert mp.status_label(screen) == "mock · plan"
    assert mp.status_label("nothing to see\n") == ""


def test_a_row_says_whether_the_question_came_and_what_was_written():
    scenario = mp.Scenario("Always-Approve", keys=(mp.SHIFT_TAB,))
    asked = mp.Observation(scenario, ready=True, label="mock · ask", asked=True)
    asked.written_after_answer = True
    row = mp.format_row(asked)
    assert "asked=yes" in row and "written without an answer=no" in row
    assert row.endswith("written after an answer=yes")
    silent = mp.Observation(scenario, ready=True, written_unasked=True)
    assert "asked=no" in mp.format_row(silent)
    assert "written without an answer=yes" in mp.format_row(silent)
    assert mp.format_row(silent).endswith("written after an answer=-")
    assert "label=(none)" in mp.format_row(silent)
