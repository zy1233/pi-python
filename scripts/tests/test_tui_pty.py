"""The PTY harness in scripts/tui_pty/ itself (no zypi needed).

The drivers there are only as good as the terminal and the process helpers under them, and they
run by hand on a machine with a built zypi, so these tests run bash and python under the same
`Term`. The `Term` ones need Linux (a pty, ``/proc``) and pyte and are skipped without them.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import signal
import socket
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
na = _load("net_audit")  # likewise: the PTY driver is imported by `main`
slp = _load("status_line_probe")  # likewise
spa = _load("status_proxy_agent")  # stdlib only


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


# ---- net_audit: what a connection audit reads, and when it fails a run ------------------

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc and sockets")


def net_row(index: int, local: str, remote: str, state: str, inode: int) -> str:
    """A row of /proc/net/tcp or udp: the columns that matter, then the kernel's fixed tail."""
    return (
        f"{index:4d}: {local} {remote} {state} 00000000:00000000 00:00000000 00000000"
        f"  1000        0 {inode} 1 0000000000000000 100 0 0 10 0"
    )


def net_table(*rows: str) -> str:
    return "  sl  local_address rem_address   st\n" + "\n".join(rows) + "\n"


def conn(proto, state, local="127.0.0.1", remote="127.0.0.1", lport=1, rport=1):
    return na.Conn(proto, (local, lport), (remote, rport), state, 1)


def test_an_address_is_read_in_the_kernels_byte_order():
    assert na.decode_address("0100007F:1F90") == ("127.0.0.1", 8080)
    assert na.decode_address("0F02A8C0:01BB") == ("192.168.2.15", 443)
    assert na.decode_address("00000000:0000") == ("0.0.0.0", 0)
    assert na.decode_address("00000000000000000000000001000000:0050") == ("::1", 80)
    assert na.decode_address("B80D0120000000000000000001000000:01BB") == ("2001:db8::1", 443)
    # IPv4-mapped: Python before 3.13 prints it in hex (::ffff:7f00:1), so compare the address.
    mapped, port = na.decode_address("0000000000000000FFFF00000100007F:0050")
    assert (ipaddress.ip_address(mapped).ipv4_mapped, port) == (
        ipaddress.ip_address("127.0.0.1"),
        80,
    )
    with pytest.raises(ValueError):
        na.decode_address("0100:0050")  # neither 4 nor 16 bytes


def test_a_net_table_is_read_row_by_row_and_a_bad_row_is_skipped():
    text = net_table(
        net_row(0, "0100007F:1F90", "00000000:0000", "0A", 111),
        net_row(1, "0402A8C0:D431", "0F02A8C0:01BB", "01", 333),
        net_row(2, "0402A8C0:D432", "0F02A8C0:01BB", "06", 444),
        "not a row",
        net_row(3, "xx:yy", "0F02A8C0:01BB", "01", 555),
    )
    rows = na.parse_net_table(text, "tcp")
    assert [(row.state, row.inode) for row in rows] == [
        ("LISTEN", 111),
        ("ESTABLISHED", 333),
        ("TIME_WAIT", 444),
    ]
    assert rows[1].local == ("192.168.2.4", 0xD431)
    assert rows[1].remote == ("192.168.2.15", 443)
    udp = na.parse_net_table(
        net_table(
            net_row(0, "0402A8C0:0044", "00000000:0000", "07", 1),
            net_row(1, "0402A8C0:D4F1", "0101A8C0:0035", "01", 2),
        ),
        "udp",
    )
    assert [row.state for row in udp] == ["UNCONNECTED", "CONNECTED"]


def test_only_a_peer_off_this_machine_makes_a_socket_outbound():
    assert not na.is_outbound(conn("tcp", "ESTABLISHED"))
    assert not na.is_outbound(conn("tcp", "ESTABLISHED", remote="::ffff:127.0.0.1"))
    assert not na.is_outbound(conn("tcp", "ESTABLISHED", remote="::1"))
    assert not na.is_outbound(conn("tcp", "LISTEN", remote="0.0.0.0", rport=0))  # no peer
    assert not na.is_outbound(conn("udp", "UNCONNECTED", remote="0.0.0.0", rport=0))
    assert na.is_outbound(conn("tcp", "ESTABLISHED", remote="192.168.2.15", rport=443))
    assert na.is_outbound(conn("tcp", "SYN_SENT", remote="192.168.2.15", rport=443))  # an attempt
    assert na.is_outbound(conn("tcp", "TIME_WAIT", remote="2001:db8::1", rport=443))
    assert na.is_outbound(conn("udp", "CONNECTED", remote="10.255.255.254", rport=53))  # DNS


def test_a_socket_on_the_wildcard_address_is_exposed_one_on_loopback_is_not():
    assert na.is_exposed(conn("tcp", "LISTEN", local="0.0.0.0", lport=8080))
    assert na.is_exposed(conn("tcp", "LISTEN", local="::", lport=8080))
    assert na.is_exposed(conn("tcp", "LISTEN", local="192.168.2.4", lport=22))
    assert not na.is_exposed(conn("tcp", "LISTEN", local="127.0.0.1", lport=8080))
    assert not na.is_exposed(conn("tcp", "LISTEN", local="::1", lport=8080))
    assert not na.is_exposed(conn("tcp", "LISTEN", local="::ffff:127.0.0.1", lport=8080))
    assert not na.is_exposed(conn("tcp", "ESTABLISHED", local="0.0.0.0", lport=8080))
    assert na.is_exposed(conn("udp", "UNCONNECTED", local="0.0.0.0", lport=5353))
    assert not na.is_exposed(conn("udp", "UNCONNECTED", local="0.0.0.0", lport=0))  # no port
    assert not na.is_exposed(conn("udp", "UNCONNECTED", local="127.0.0.1", lport=5353))


def test_the_log_names_each_remote_by_number_and_prints_no_address():
    log = na.Log(started=100.0)
    gateway = {"remote": "192.168.2.15", "rport": 443}
    log.record("agent", conn("tcp", "ESTABLISHED", **gateway), 101.0)
    log.record("agent", conn("tcp", "ESTABLISHED", **gateway), 102.0)  # the same remote again
    log.record("agent", conn("tcp", "SYN_SENT", **gateway), 103.0)  # same remote, another state
    log.record("agent", conn("udp", "CONNECTED", remote="10.255.255.254", rport=53), 104.0)
    log.record("zypi", conn("tcp", "ESTABLISHED"), 105.0)  # loopback: nothing to record
    seen = log.of("agent")
    assert sorted((s.remote, s.proto, s.port) for s in seen) == [
        ("remote#1", "tcp", 443),
        ("remote#2", "udp", 53),
    ]
    assert log.of("zypi") == []
    tcp = next(s for s in seen if s.proto == "tcp")
    assert tcp.states == {"ESTABLISHED", "SYN_SENT"}
    assert tcp.first == pytest.approx(1.0)  # seconds after the log was made
    text = na.describe(seen)
    assert "remote#1 tcp/443 ESTABLISHED+SYN_SENT first @+1.0s" in text
    assert "192.168" not in text and "10.255" not in text
    # A listener is an exposure, not an outbound connection, and is kept apart from them.
    log.record("agent", conn("tcp", "LISTEN", local="0.0.0.0", lport=8080), 106.0)
    assert [s.port for s in log.of("agent", "exposed")] == [8080]
    assert len(log.of("agent")) == 2


def test_a_role_is_told_from_the_command_line():
    zypi = "/opt/zypi/bin/zypi"
    assert na.role_of(zypi, zypi) == "zypi"
    assert na.role_of("/elsewhere/zypi --sandbox strict", zypi) == "zypi"
    assert na.role_of("/venv/bin/python -m pi_agent_cli", zypi) == "agent"
    assert na.role_of(f"bwrap --unshare-net {zypi}", zypi) == "wrapper"
    assert na.role_of("git status --porcelain", zypi) == "other"
    assert na.role_of("", zypi) == "other"  # a process that is exiting
    assert na.describe_roles({"zypi": {"zypi"}, "other": {"sh", "git"}, "agent": {"python"}}) == (
        "agent, other (git, sh), zypi"
    )


def healthy_log(*, agent_connects: bool = False):
    """A log of a run in which the sampler saw zypi and the agent, and only the agent talked."""
    log = na.Log(started=0.0)
    log.roles = {"zypi": {"zypi"}, "agent": {"python"}}
    log.ticks, log.last = 100, 1.0
    if agent_connects:
        log.record("agent", conn("tcp", "ESTABLISHED", remote="192.168.2.15", rport=80), 0.5)
    return log


def failed(checks) -> set[str]:
    return {check.name for check in checks if not check.ok}


def test_a_clean_mock_run_and_a_clean_real_run_pass_every_check():
    assert failed(na.judge(healthy_log(), real=False)) == set()
    assert failed(na.judge(healthy_log(agent_connects=True), real=True)) == set()


def test_a_real_run_without_the_agents_connection_proves_nothing():
    # No connection seen at all, or one that never got past the handshake: the sampler or the
    # turn may have missed the traffic, so a clean zypi means nothing.
    log = healthy_log()
    log.record("agent", conn("tcp", "SYN_SENT", remote="192.168.2.15", rport=80), 0.5)
    for run in (healthy_log(), log):
        names = failed(na.judge(run, real=True))
        assert len(names) == 1 and next(iter(names)).startswith("positive control")


def test_a_connection_of_zypi_fails_a_run_in_either_mode():
    for real in (False, True):
        log = healthy_log(agent_connects=real)
        log.record("zypi", conn("udp", "CONNECTED", remote="10.255.255.254", rport=53), 0.2)
        assert failed(na.judge(log, real=real)) == {
            "zypi has no connection to a non-loopback address"
        }


def test_the_agent_must_stay_quiet_when_the_llm_is_the_mock():
    names = failed(na.judge(healthy_log(agent_connects=True), real=False))
    assert names == {"the agent has no connection to a non-loopback address (mock LLM)"}


def test_a_tool_or_a_wrapper_that_connects_fails_a_run():
    for role in ("other", "wrapper"):
        log = healthy_log()
        log.record(role, conn("tcp", "ESTABLISHED", remote="192.168.2.15", rport=80), 0.2)
        assert failed(na.judge(log, real=False)) == {
            f"no `{role}` process has a connection to a non-loopback address"
        }


def test_a_listener_on_every_interface_fails_a_run():
    log = healthy_log()
    log.record("zypi", conn("tcp", "LISTEN", local="0.0.0.0", lport=9000), 0.2)
    assert failed(na.judge(log, real=False)) == {"`zypi` listens on no non-loopback address"}


def test_a_run_in_which_the_sampler_never_saw_zypi_fails():
    assert "the sampler saw zypi itself" in failed(na.judge(na.Log(started=0.0), real=False))


def test_a_real_turn_is_answered_only_with_a_reply_and_no_error_line():
    prompt = "  > Reply with the single word pong.      1:09 PM\n"
    assert na.real_reply(prompt + "  pong      1:09 PM\n  Worked for 3.6s\n")[0]
    refused = prompt + "  Error: Error code: 401 - {'error': 'Invalid token'}\n  Worked for 0.7s\n"
    ok, detail = na.real_reply(refused)
    assert not ok and "an error line is shown" in detail
    assert "401" not in detail  # the error text can name the host: it is not quoted
    assert not na.real_reply(prompt + "  Worked for 0.1s\n")[0]  # the footer alone is no reply


def test_a_real_run_takes_the_model_table_and_no_secret_with_it():
    model = {
        "provider": "deepseek",
        "id": "m-1",
        "base_url": "http://gateway.example/v1",
        "api_key_env": "SOME_VARIABLE",
        "api_key": "sk-not-for-the-copy",
        "auth_token": "t-not-for-the-copy",
        "supports_images": True,
        "budget": 3,
        "nested": {"x": 1},
        "odd": 'say "hi" \\ bye',
    }
    text = na.agent_toml(model)
    assert "not-for-the-copy" not in text
    assert tomllib.loads(text) == {
        "model": {
            "provider": "deepseek",
            "id": "m-1",
            "base_url": "http://gateway.example/v1",
            "api_key_env": "SOME_VARIABLE",  # the name of the variable, not the key
            "supports_images": True,
            "budget": 3,
            "odd": 'say "hi" \\ bye',
        }
    }


def test_the_model_table_is_all_that_is_read_from_the_users_file(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text('permission = "auto"\n[model]\nid = "m"\n[skills]\npaths = ["x"]\n', "utf-8")
    assert na.model_table(path) == {"id": "m"}
    path.write_text('permission = "auto"\n', "utf-8")
    with pytest.raises(SystemExit, match=r"no \[model\] table"):
        na.model_table(path)


@linux_only
def test_the_process_tree_reaches_a_grandchild_and_names_it_by_command_line():
    child = subprocess.Popen(["sh", "-c", "sleep 30 & wait"])
    try:
        deadline = time.monotonic() + 10
        tree = na.process_tree(child.pid)
        while len(tree) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
            tree = na.process_tree(child.pid)
        assert sorted(tree.values()) == ["sh -c sleep 30 & wait", "sleep 30"]
    finally:
        child.kill()
        child.wait()


@linux_only
def test_the_sampler_finds_a_socket_open_on_every_interface_in_a_child_process():
    # End to end on a live system, with no network: /proc/<pid>/fd joined with /proc/<pid>/net.
    code = (
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.bind(('0.0.0.0', 0))\n"
        "s.listen()\n"
        "print(s.getsockname()[1], flush=True)\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        port = int(child.stdout.readline())
        log = na.Log()
        log.sample("/no/such/zypi", os.getpid(), time.monotonic())
        assert [s.port for s in log.of("other", "exposed") if s.port == port] == [port]
        assert log.ticks == 1 and "other" in log.roles
    finally:
        child.kill()
        child.wait()


@linux_only
def test_the_sampler_does_not_record_traffic_on_loopback():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    client = socket.create_connection(server.getsockname())
    peer, _ = server.accept()
    try:
        log = na.Log()
        log.sample("/no/such/zypi", os.getpid(), time.monotonic())
        assert all(s.port != server.getsockname()[1] for s in log.seen.values())
    finally:
        for sock in (peer, client, server):
            sock.close()


# ---- status_line_probe and status_proxy_agent: the status row and the message behind it --


def test_the_status_line_scenarios_cover_both_row_types_with_and_without_a_status_from_the_agent():
    assert {(s.kind, s.injected) for s in slp.SCENARIOS} == {
        (kind, injected) for kind in ("command", "builtin") for injected in (False, True)
    }
    assert len({s.name for s in slp.SCENARIOS}) == len(slp.SCENARIOS)


def test_the_probe_names_the_model_the_proxy_sends():
    assert slp.PROXY_MODEL == spa.MODEL
    assert slp.AGENT_MODEL != slp.PROXY_MODEL  # or the precedence test could not tell them apart


def test_a_scenarios_config_text_is_a_status_line_section(tmp_path):
    script = tmp_path / "statusline.sh"
    for scenario in slp.SCENARIOS:
        section = tomllib.loads(slp.config_text(scenario, script))["ui"]["status_line"]
        assert section["type"] == scenario.kind
        if scenario.kind == "command":
            assert section["command"] == str(script)
        else:
            assert section["items"] == ["cwd", "model", "context"]


def test_the_row_patterns_match_the_row_and_nothing_else_on_the_screen():
    def scenario(kind, injected):
        return next(s for s in slp.SCENARIOS if (s.kind, s.injected) == (kind, injected))

    with_status, without = scenario("builtin", True), scenario("builtin", False)
    assert re.search(with_status.row, "  work  │  proxy-model  │  25% ctx")
    assert not re.search(with_status.row, "  work  │  mock-a")  # the model must be the agent's
    assert re.search(without.row, "  work  │  mock-a")  # the client's own model, no context item
    assert not re.search(without.row, "  work  │  proxy-model")
    for elsewhere in ("  /tmp/pi-statusline-x/work", "  │ >                       │"):
        assert not re.search(without.row, elsewhere)
    command = scenario("command", False)
    assert re.search(command.row, f"  {slp.ROW_TEXT}")
    assert not re.search(command.row, "  STATUSROW")


def test_a_run_is_what_the_page_says_when_the_row_is_drawn_from_the_right_facts():
    def says(kind, injected, **seen):
        return slp.verdict(slp.Scenario(kind, injected), **seen)[0]

    drawn = {"replied": True, "ran": True, "shown": True}
    # The row is drawn with the plain agent as well as with one that sends a status.
    for injected in (False, True):
        assert says("command", injected, **drawn)
        assert says("builtin", injected, replied=True, ran=False, shown=True)  # no script to run
        assert not says("command", injected, **{**drawn, "shown": False})  # no row on the screen
        assert not says("builtin", injected, replied=True, ran=False, shown=False)
        assert not says("command", injected, **{**drawn, "ran": False})  # the script did not run
        assert not says("command", injected, **drawn, problems=["session_id is missing"])
        # A turn that was never answered tested nothing.
        assert not says("command", injected, **{**drawn, "replied": False})


def test_the_fields_a_command_script_was_given_are_read_from_its_payload():
    assert slp.payload_fields('{"b": 1, "a": {"c": 2}}') == ["a", "b"]
    assert slp.payload_fields("not json") == []
    assert slp.payload_fields("[1, 2]") == []
    assert slp.payload_of('{"a": 1}') == {"a": 1}
    assert slp.payload_of("not json") == {} and slp.payload_of("[1]") == {}


def _payload(model, **more):
    """What a script is given when the session is in /tmp/x/work and the model is `model`."""
    return {
        "schema_version": 1,
        "cwd": "/tmp/x/work",
        "session_id": "s-1",
        "version": "0.5.0",
        "model": {"id": model, "display_name": model},
        "workspace": {"current_dir": "/tmp/x/work"},
        "cost": {"total_duration_ms": 10},
        "context_window": {},
        **more,
    }


def test_a_payload_is_checked_for_the_facts_the_client_knows():
    plain = slp.Scenario("command", False)
    assert slp.payload_problems(plain, _payload("mock-a")) == []
    # The other scenario's model is a problem: the precedence went the wrong way.
    assert slp.payload_problems(plain, _payload("proxy-model")) == [
        "model.display_name is not 'mock-a'"
    ]
    assert slp.payload_problems(slp.Scenario("command", True), _payload("proxy-model")) == []
    # Every fact that is missing is named.
    assert len(slp.payload_problems(plain, {})) == 6


def test_a_payload_has_no_number_the_client_cannot_know_unless_the_agent_sent_it():
    plain, proxy = slp.Scenario("command", False), slp.Scenario("command", True)
    for field, section in (
        ("used_percentage", "context_window"),
        ("context_tokens", "context_window"),
        ("context_window_size", "context_window"),
        ("total_cost_usd", "cost"),
    ):
        number = _payload("mock-a", **{section: {field: 0}})
        assert len(slp.payload_problems(plain, number)) == 1, field  # zero is a made-up number
        # An agent that sent the number is believed.
        agent_number = _payload("proxy-model", **{section: {field: 25}})
        assert slp.payload_problems(proxy, agent_number) == [], field


NEW_SESSION = b'{"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s-1"}}\n'
AGENT_UPDATE = b'{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s-1"}}\n'
END_OF_TURN = b'{"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}\n'


def test_the_proxy_adds_a_status_message_after_session_new_and_after_each_turn():
    out = list(spa.with_status([NEW_SESSION, AGENT_UPDATE, END_OF_TURN], "/work"))
    assert [out[0], out[2], out[3]] == [NEW_SESSION, AGENT_UPDATE, END_OF_TURN]
    assert len(out) == 5
    for note in (json.loads(out[1]), json.loads(out[4])):
        assert note["method"] == "_pi/session_notification"  # the carrier the pager reads
        assert note["params"]["sessionId"] == "s-1"
        update = note["params"]["update"]
        assert update["sessionUpdate"] == "session_status"
        assert update["cwd"] == "/work" and update["workspace"]["current_dir"] == "/work"
        assert update["model"]["display_name"] == spa.MODEL


def test_the_proxy_changes_nothing_else_the_agent_says():
    lines = [
        AGENT_UPDATE,
        b"not json\n",
        b'{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}\n',
        b'{"jsonrpc": "2.0", "id": 9, "error": {"code": -32601}}\n',
        b"[1, 2]\n",
        END_OF_TURN,  # no session is known yet, so there is none to report a status for
    ]
    assert list(spa.with_status(lines, "/w")) == lines


def test_the_proxy_runs_the_agent_and_passes_input_and_exit_status_through(tmp_path):
    package = tmp_path / "pi_agent_cli"  # a stand-in that the proxy finds first on PYTHONPATH
    package.mkdir()
    (package / "__init__.py").write_text("", "utf-8")
    (package / "__main__.py").write_text(
        "import sys\n"
        'print(\'{"jsonrpc": "2.0", "id": 1, "result": {"sessionId": "s-9"}}\', flush=True)\n'
        "for line in sys.stdin:\n"
        "    print('echo:' + line.strip(), flush=True)\n"
        "sys.exit(7)\n",
        "utf-8",
    )
    run = subprocess.run(
        [sys.executable, str(TUI_PTY / "status_proxy_agent.py")],
        input="hello\n",
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )
    lines = run.stdout.splitlines()
    assert json.loads(lines[0])["result"]["sessionId"] == "s-9"
    assert json.loads(lines[1])["params"]["update"]["sessionUpdate"] == "session_status"
    assert lines[2] == "echo:hello"
    assert run.returncode == 7
