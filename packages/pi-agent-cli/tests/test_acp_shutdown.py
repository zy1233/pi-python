"""Stopping the stdio agent must reap the tools it is running.

A client that closes the agent's stdin, or sends it SIGTERM (or SIGHUP, which a closing terminal
sends), in the middle of a ``bash`` call must not leave that process behind. The Rust TUI relies
on this: it closes stdin first and kills the agent only if it does not exit by itself. SIGKILL
cannot be handled, so a tool outlives an agent that was killed that way; that case is
deliberately not tested.

The agent under test is ``_tool_agent.py`` (the real stdio loop with a scripted LLM turn).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.schema import DeniedOutcome, RequestPermissionResponse

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals and process groups"),
]

HELPER = Path(__file__).with_name("_tool_agent.py")
WIRE_TIMEOUT = 60.0
EXIT_TIMEOUT = 15.0
TOOL_REAP_TIMEOUT = 5.0


class _SilentClient:
    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        return None

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_tool_pid(pidfile: Path) -> int:
    while True:
        if pidfile.exists():
            text = pidfile.read_text().strip()
            if text.isdigit():
                return int(text)
        await asyncio.sleep(0.05)


async def _assert_tool_reaped(pid: int) -> None:
    deadline = asyncio.get_running_loop().time() + TOOL_REAP_TIMEOUT
    while _alive(pid):
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(f"the tool process {pid} outlived the agent")
        await asyncio.sleep(0.05)


@asynccontextmanager
async def agent_running_a_tool(
    home: Path, *, stuck_thread: bool = False
) -> AsyncIterator[tuple[asyncio.subprocess.Process, int]]:
    """An agent process in the middle of a ``bash`` call; yields it and the tool's pid.

    With ``stuck_thread`` the turn blocks in an uncancellable thread instead, and the pid is the
    agent's own.
    """
    pidfile = home / "tool.pid"
    env = {"PI_HOME": str(home), "PI_TEST_TOOL_PIDFILE": str(pidfile)}
    if stuck_thread:
        env["PI_TEST_STUCK_THREAD"] = "1"
    async with spawn_agent_process(
        _SilentClient(),
        sys.executable,
        str(HELPER),
        env=env,
        cwd=home,
        transport_kwargs={"stderr": subprocess.DEVNULL, "shutdown_timeout": 10.0},
    ) as (conn, process):
        await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), WIRE_TIMEOUT)
        session = await asyncio.wait_for(
            conn.new_session(cwd=str(home), mcp_servers=[]), WIRE_TIMEOUT
        )
        prompt = asyncio.ensure_future(
            conn.prompt(session_id=session.session_id, prompt=[text_block("run it")])
        )
        tool_pid: int | None = None
        try:
            tool_pid = await asyncio.wait_for(_wait_for_tool_pid(pidfile), WIRE_TIMEOUT)
            assert _alive(tool_pid)
            yield process, tool_pid
        finally:
            prompt.cancel()
            with contextlib.suppress(BaseException):
                await prompt
            if tool_pid is not None and _alive(tool_pid):  # a failing test must not leak it
                with contextlib.suppress(ProcessLookupError):
                    os.kill(tool_pid, signal.SIGKILL)


async def test_closing_stdin_stops_the_agent_and_reaps_the_running_tool(tmp_path):
    async with agent_running_a_tool(tmp_path) as (process, tool_pid):
        assert process.stdin is not None
        process.stdin.close()
        await asyncio.wait_for(process.wait(), EXIT_TIMEOUT)
        assert process.returncode == 0
        await _assert_tool_reaped(tool_pid)


@pytest.mark.parametrize("stop_signal", ["SIGTERM", "SIGHUP"])
async def test_stop_signals_stop_the_agent_and_reap_the_running_tool(tmp_path, stop_signal):
    async with agent_running_a_tool(tmp_path) as (process, tool_pid):
        process.send_signal(getattr(signal, stop_signal))
        await asyncio.wait_for(process.wait(), EXIT_TIMEOUT)
        assert process.returncode == 0
        await _assert_tool_reaped(tool_pid)


async def test_a_repeated_signal_stops_an_agent_whose_shutdown_hangs(tmp_path):
    """The first SIGTERM starts a graceful stop; if that hangs, the second one must still kill."""
    async with agent_running_a_tool(tmp_path, stuck_thread=True) as (process, _agent_pid):
        process.send_signal(signal.SIGTERM)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(process.wait()), 1.5)  # still shutting down
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), EXIT_TIMEOUT)
        assert process.returncode == -signal.SIGTERM
