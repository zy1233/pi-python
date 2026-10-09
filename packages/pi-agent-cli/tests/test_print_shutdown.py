"""A ``-p`` run must stop, and reap its tools, when it is told to or when its parent goes away.

``zypi -p`` runs ``python -m pi_agent_cli -p`` as a child that shares the user's terminal, so
there is no EOF on stdin to tell the agent that its client is gone (the stdio agent has one; see
``test_acp_shutdown.py``). A stop signal, or the death of the process that started the run (zypi
killed by anything, SIGKILL included), has to end the run the same way: the turn is cancelled and
the process group of the ``bash`` tool it is running is killed. SIGKILL of the agent itself cannot
be handled and is deliberately not tested.

The agent under test is ``_print_agent.py`` (the real ``main()`` with a scripted LLM turn).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from pi_agent_cli.__main__ import PARENT_PID_ENV, PARENT_POLL_SECONDS, _take_parent_pid

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="needs POSIX signals and process groups"
)

HELPER = Path(__file__).with_name("_print_agent.py")
START_TIMEOUT = 90.0  # the agent imports the whole LLM stack before it does anything
EXIT_TIMEOUT = 15.0
TOOL_REAP_TIMEOUT = 5.0

# A stand-in for zypi: it starts the agent, naming itself as the parent, and then does nothing.
LAUNCHER = """
import os, subprocess, sys, time
env = dict(os.environ, PI_AGENT_PARENT_PID=str(os.getpid()))
subprocess.Popen([sys.executable, sys.argv[1]], env=env)
time.sleep(600)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is dead; nobody has collected it only because its parent was killed too
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True  # no /proc here; kill(0) is all there is
    return state != "Z"


def _wait_until(done, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(0.05)
    return done()


class _Run:
    """A ``-p`` agent process, started with ``PI_AGENT_PARENT_PID`` set (or not)."""

    def __init__(self, home: Path, *, parent_pid: int | None = None, via_launcher: bool = False):
        self.home = home
        self.tool_pidfile = home / "tool.pid"
        self.envfile = home / "tool.env"
        self.agent_pidfile = home / "agent.pid"
        self.stderr_path = home / "stderr.log"
        env = {k: v for k, v in os.environ.items() if k != PARENT_PID_ENV}
        env.update(
            PI_HOME=str(home),
            PI_USE_MOCK="1",
            PI_TEST_TOOL_PIDFILE=str(self.tool_pidfile),
            PI_TEST_TOOL_ENVFILE=str(self.envfile),
            PI_TEST_AGENT_PIDFILE=str(self.agent_pidfile),
        )
        if parent_pid is not None:
            env[PARENT_PID_ENV] = str(parent_pid)
        argv = [sys.executable, str(HELPER)]
        if via_launcher:
            argv = [sys.executable, "-c", LAUNCHER, str(HELPER)]
        with self.stderr_path.open("wb") as stderr:
            # Its own session, so that cleanup can take the whole group down.
            self.proc = subprocess.Popen(
                argv,
                env=env,
                cwd=home,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
            )

    def _diagnostics(self) -> str:
        with contextlib.suppress(OSError):
            return self.stderr_path.read_text(errors="replace")[-2000:]
        return ""

    def _pid_from(self, path: Path) -> int | None:
        with contextlib.suppress(OSError, ValueError):
            return int(path.read_text().strip())
        return None

    @property
    def agent_pid(self) -> int:
        pid = self._pid_from(self.agent_pidfile)
        assert pid is not None, "the agent never wrote its pid"
        return pid

    @property
    def tool_pid(self) -> int:
        pid = self._pid_from(self.tool_pidfile)
        assert pid is not None, "the tool never started"
        return pid

    def wait_for_tool(self) -> int:
        if not _wait_until(lambda: self._pid_from(self.tool_pidfile) is not None, START_TIMEOUT):
            pytest.fail(
                f"the tool never started (agent status {self.proc.poll()}):\n{self._diagnostics()}"
            )
        return self.tool_pid

    def wait_exit(self) -> int:
        try:
            return self.proc.wait(EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the agent did not stop within {EXIT_TIMEOUT}s:\n{self._diagnostics()}")

    def assert_gone(self, pid: int, what: str) -> None:
        if not _wait_until(lambda: not _alive(pid), TOOL_REAP_TIMEOUT):
            pytest.fail(f"the {what} {pid} is still running")

    def close(self) -> None:
        """A failing test must not leave processes behind."""
        pid = self._pid_from(self.tool_pidfile)
        if pid is not None and _alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(5)


@contextlib.contextmanager
def _running_tool(home: Path, **kwargs) -> Iterator[_Run]:
    """A ``-p`` agent in the middle of a ``bash`` call."""
    run = _Run(home, **kwargs)
    try:
        run.wait_for_tool()
        assert _alive(run.tool_pid)
        yield run
    finally:
        run.close()


@pytest.mark.parametrize("stop_signal", ["SIGTERM", "SIGHUP"])
def test_a_stop_signal_stops_the_run_and_reaps_the_running_tool(tmp_path, stop_signal):
    number = getattr(signal, stop_signal)
    # The parent watch is armed here (this process started the agent), and must not get in the way.
    with _running_tool(tmp_path, parent_pid=os.getpid()) as run:
        # What the tool sees: the variable is the agent's business, not something to inherit.
        assert run.envfile.read_text().strip() == "unset"
        tool_pid = run.tool_pid
        run.proc.send_signal(number)
        assert run.wait_exit() == 128 + number
        run.assert_gone(tool_pid, "tool process")


def test_the_run_stops_when_the_process_that_started_it_is_killed(tmp_path):
    with _running_tool(tmp_path, via_launcher=True) as run:
        tool_pid, agent_pid = run.tool_pid, run.agent_pid
        os.kill(run.proc.pid, signal.SIGKILL)  # the launcher: nothing gets to tell the agent
        run.proc.wait(5)
        run.assert_gone(agent_pid, "agent")
        run.assert_gone(tool_pid, "tool process")


def test_a_live_process_that_is_not_the_parent_is_not_waited_on(tmp_path):
    """A wrapper script between zypi and the agent is the common case: stay up."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        with _running_tool(tmp_path, parent_pid=other.pid) as run:
            time.sleep(PARENT_POLL_SECONDS * 4)
            assert run.proc.poll() is None
            assert _alive(run.tool_pid)
    finally:
        other.kill()
        other.wait()


def _dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def test_a_parent_that_is_already_gone_stops_the_run_before_it_starts_a_tool(tmp_path):
    run = _Run(tmp_path, parent_pid=_dead_pid())
    try:
        assert run.wait_exit() == 128 + signal.SIGTERM
        assert not run.tool_pidfile.exists()
    finally:
        run.close()


# ---- which pid counts -------------------------------------------------------------------------


def test_take_parent_pid_accepts_the_parent_and_a_process_that_no_longer_exists(monkeypatch):
    parent = os.getppid()
    monkeypatch.setenv(PARENT_PID_ENV, str(parent))
    assert _take_parent_pid() == parent
    assert PARENT_PID_ENV not in os.environ

    gone = _dead_pid()
    monkeypatch.setenv(PARENT_PID_ENV, str(gone))
    assert _take_parent_pid() == gone
    assert PARENT_PID_ENV not in os.environ


@pytest.mark.parametrize("value", [None, "", "abc", "-3", "0", "12.5", "99999999999", "²"])
def test_take_parent_pid_ignores_what_is_not_a_pid(monkeypatch, value):
    monkeypatch.delenv(PARENT_PID_ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(PARENT_PID_ENV, value)
    assert _take_parent_pid() is None
    assert PARENT_PID_ENV not in os.environ


def test_take_parent_pid_ignores_a_live_process_that_is_not_the_parent(monkeypatch):
    assert os.getpid() != os.getppid()
    monkeypatch.setenv(PARENT_PID_ENV, str(os.getpid()))
    assert _take_parent_pid() is None
    assert PARENT_PID_ENV not in os.environ
