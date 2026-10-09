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
