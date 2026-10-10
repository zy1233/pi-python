"""Tool calls over real stdio: the permission round trip and ``session/cancel``.

``test_acp_stdio_contract.py`` cannot cover these: the mock LLM never calls a tool. The agent under
test here is ``_tool_agent.py`` (the real stdio loop with a scripted LLM turn that calls one
tool), driven the way the Rust TUI drives the agent: the TUI answers ``session/request_permission``
and sends ``session/cancel`` on the same connection that carries the prompt.

What the pager depends on, and what is asserted:

* in ``ask`` mode a call to a tool that does not declare itself harmless reaches the client as
  ``session/request_permission`` *before* the tool runs, after the call was announced as a
  ``tool_call`` update, with the two options the pager knows and the tool's own input;
* ``allow-once`` runs the tool; ``reject-once`` and a ``cancelled`` outcome do not, and either way
  the turn ends normally (the model is told, and answers);
* ``session/cancel`` ends the running prompt with ``stopReason: "cancelled"`` and reaps the tool;
  it is a notification, so it is never answered, and with nothing running it is harmless.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.client.connection import ClientSideConnection
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)

pytestmark = pytest.mark.asyncio

HELPER = Path(__file__).with_name("_tool_agent.py")
WIRE_TIMEOUT = 60.0
TOOL_REAP_TIMEOUT = 5.0
CALL_ID = "call_write"

Answer = Callable[[], RequestPermissionResponse | Awaitable[RequestPermissionResponse]]


def allow_once() -> RequestPermissionResponse:
    return RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
    )


def reject_once() -> RequestPermissionResponse:
    return RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id="reject-once")
    )


def cancelled() -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


class ToolClient:
    """An ACP client that records the traffic in order and answers permission requests.

    ``trace`` holds ``("update", kind, tool_call_id)`` and ``("permission", tool_call_id)``
    entries, in the order they arrived. ``marker`` is the file the tool writes: whether it
    exists when a request comes in says whether the tool ran before it was allowed to.
    ``answer`` may be a coroutine function, to keep a question open.
    """

    def __init__(self, answer: Answer, marker: Path | None = None) -> None:
        self.answer = answer
        self.marker = marker
        self.trace: list[tuple[str, ...]] = []
        self.requests: list[tuple[str, ToolCallUpdate, list[PermissionOption]]] = []
        self.tool_ran_before_request: list[bool] = []
        self.asked = asyncio.Event()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        kind = getattr(update, "session_update", "")
        self.trace.append(("update", kind, getattr(update, "tool_call_id", "")))

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        self.trace.append(("permission", tool_call.tool_call_id))
        self.requests.append((session_id, tool_call, options))
        if self.marker is not None:
            self.tool_ran_before_request.append(self.marker.exists())
        self.asked.set()
        response = self.answer()
        if inspect.isawaitable(response):
            response = await response
        return response

    def tool_updates(self, kind: str) -> list[str]:
        """The tool-call ids of the ``session/update`` notifications of one kind, in order."""
        return [entry[2] for entry in self.trace if entry[:2] == ("update", kind)]


@asynccontextmanager
async def tool_agent(
    home: Path, client: ToolClient, *, permission: str, call: dict[str, Any] | None = None
) -> AsyncIterator[tuple[ClientSideConnection, asyncio.subprocess.Process, str]]:
    """The scripted agent with a session opened in ``home``; yields the connection, the process
    and the session id. Without ``call`` the turn runs the default long ``bash`` call, which
    records its pid in ``home / "tool.pid"``."""
    env = {
        "PI_HOME": str(home),
        "PI_TEST_PERMISSION": permission,
        "PI_TEST_TOOL_PIDFILE": str(home / "tool.pid"),
    }
    if call is not None:
        env["PI_TEST_TOOL_CALL"] = json.dumps(call)
    async with spawn_agent_process(
        client,
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
        yield conn, process, session.session_id


def write_call(marker: Path) -> dict[str, Any]:
    return {
        "id": CALL_ID,
        "name": "write",
        "arguments": {"path": str(marker), "content": "the tool ran"},
    }


async def run_write_prompt(
    tmp_path: Path, answer: Answer, *, permission: str = "ask"
) -> tuple[ToolClient, Any, Path]:
    """One prompt whose scripted turn writes a file; returns the traffic and the file's path."""
    marker = tmp_path / "marker.txt"
    client = ToolClient(answer, marker)
    async with tool_agent(tmp_path, client, permission=permission, call=write_call(marker)) as (
        conn,
        _process,
        session_id,
    ):
        result = await asyncio.wait_for(
            conn.prompt(session_id=session_id, prompt=[text_block("write it")]), WIRE_TIMEOUT
        )
    return client, result, marker


async def test_ask_mode_puts_the_tool_call_to_the_client_before_the_tool_runs(tmp_path):
    client, result, marker = await run_write_prompt(tmp_path, allow_once)

    ((_session, tool_call, options),) = client.requests
    assert tool_call.tool_call_id == CALL_ID
    assert tool_call.title == "write"
    assert tool_call.kind == "edit"
    assert tool_call.raw_input == {"path": str(marker), "content": "the tool ran"}
    assert [(o.option_id, o.kind) for o in options] == [
        ("allow-once", "allow_once"),
        ("reject-once", "reject_once"),
    ]
    # The request names a call the client has been shown, and the tool waited for the answer.
    assert client.trace.index(("update", "tool_call", CALL_ID)) < client.trace.index(
        ("permission", CALL_ID)
    )
    assert client.tool_ran_before_request == [False]
    assert result.stop_reason == "end_turn"


async def test_allow_once_runs_the_tool_and_the_turn_goes_on_to_the_models_answer(tmp_path):
    client, result, marker = await run_write_prompt(tmp_path, allow_once)

    assert marker.read_text(encoding="utf-8") == "the tool ran"
    assert client.tool_updates("tool_call_update")[-1] == CALL_ID  # reported as finished
    assert result.stop_reason == "end_turn"
    assert len(client.requests) == 1  # one question for one call


@pytest.mark.parametrize("answer", [reject_once, cancelled], ids=["reject-once", "cancelled"])
async def test_a_refusal_keeps_the_tool_from_running_and_the_turn_still_ends(tmp_path, answer):
    client, result, marker = await run_write_prompt(tmp_path, answer)

    assert not marker.exists()
    assert client.tool_updates("tool_call_update")[-1] == CALL_ID  # the call is closed, as failed
    assert result.stop_reason == "end_turn"  # the model was told, and answered


async def test_auto_mode_never_asks(tmp_path):
    # This client would refuse, if it were asked.
    client, result, marker = await run_write_prompt(tmp_path, cancelled, permission="auto")

    assert client.requests == []
    assert marker.read_text(encoding="utf-8") == "the tool ran"
    assert result.stop_reason == "end_turn"


async def test_cancel_while_a_permission_question_is_open_ends_the_prompt_as_cancelled(tmp_path):
    """What the pager does on Esc: ``session/cancel``, then ``cancelled`` for what is open."""
    release = asyncio.Event()

    async def answer_when_released() -> RequestPermissionResponse:
        await release.wait()
        return cancelled()

    marker = tmp_path / "marker.txt"
    client = ToolClient(answer_when_released, marker)
    async with tool_agent(tmp_path, client, permission="ask", call=write_call(marker)) as (
        conn,
        _process,
        session_id,
    ):
        prompt = asyncio.ensure_future(
            conn.prompt(session_id=session_id, prompt=[text_block("write it")])
        )
        await asyncio.wait_for(client.asked.wait(), WIRE_TIMEOUT)
        await conn.cancel(session_id=session_id)
        release.set()
        result = await asyncio.wait_for(prompt, WIRE_TIMEOUT)

    assert result.stop_reason == "cancelled"
    assert not marker.exists()


async def test_cancel_with_nothing_running_is_harmless(tmp_path):
    client = ToolClient(cancelled)
    async with tool_agent(tmp_path, client, permission="ask") as (conn, process, session_id):
        await conn.cancel(session_id=session_id)  # a notification: there is no reply to wait for
        await conn.cancel(session_id="no-such-session")  # nor an error for a session it lacks
        listed = await asyncio.wait_for(conn.list_sessions(), WIRE_TIMEOUT)  # still answering
        assert process.returncode is None
    assert [s.session_id for s in listed.sessions] == [session_id]


# --- a running tool: needs POSIX processes (``bash``, ``sleep``, ``kill -0``) --------------------


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


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals and process groups")
async def test_cancel_ends_a_running_tool_turn_as_cancelled_and_reaps_the_tool(tmp_path):
    client = ToolClient(cancelled)
    tool_pid: int | None = None
    async with tool_agent(tmp_path, client, permission="auto") as (conn, process, session_id):
        prompt = asyncio.ensure_future(
            conn.prompt(session_id=session_id, prompt=[text_block("run it")])
        )
        try:
            tool_pid = await asyncio.wait_for(
                _wait_for_tool_pid(tmp_path / "tool.pid"), WIRE_TIMEOUT
            )
            assert _alive(tool_pid)

            await conn.cancel(session_id=session_id)
            result = await asyncio.wait_for(prompt, WIRE_TIMEOUT)

            assert result.stop_reason == "cancelled"
            deadline = asyncio.get_running_loop().time() + TOOL_REAP_TIMEOUT
            while _alive(tool_pid):
                assert asyncio.get_running_loop().time() < deadline, "the tool outlived the cancel"
                await asyncio.sleep(0.05)
            assert process.returncode is None  # the agent stays up for the next prompt
        finally:
            if not prompt.done():
                prompt.cancel()
            with contextlib.suppress(BaseException):
                await prompt
            if tool_pid is not None and _alive(tool_pid):  # a failing test must not leak it
                with contextlib.suppress(ProcessLookupError):
                    os.kill(tool_pid, signal.SIGKILL)
