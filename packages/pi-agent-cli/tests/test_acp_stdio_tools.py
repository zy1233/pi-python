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
from acp import PROTOCOL_VERSION, RequestError, spawn_agent_process, text_block
from acp.client.connection import ClientSideConnection
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)

from pi_agent_cli.auto_mode import AUDIT_ENTRY_TYPE, ENTRY_MESSAGE, shell_problems

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
        self.updates: list[Any] = []
        self.requests: list[tuple[str, ToolCallUpdate, list[PermissionOption]]] = []
        self.tool_ran_before_request: list[bool] = []
        self.asked = asyncio.Event()
        self.mode_pushed = asyncio.Event()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        kind = getattr(update, "session_update", "")
        self.trace.append(("update", kind, getattr(update, "tool_call_id", "")))
        self.updates.append(update)
        if kind == "config_option_update":
            self.mode_pushed.set()

    def agent_text(self) -> str:
        """Everything the agent said to the user as plain messages (notices), in order."""
        return "\n".join(
            update.content.text
            for update in self.updates
            if getattr(update, "session_update", "") == "agent_message_chunk"
            and getattr(update.content, "type", "") == "text"
        )

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
    home: Path,
    client: ToolClient,
    *,
    permission: str,
    call: dict[str, Any] | None = None,
    project: Path | None = None,
    meta: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[ClientSideConnection, asyncio.subprocess.Process, str]]:
    """The scripted agent with a session opened; yields the connection, the process and the
    session id. Without ``call`` the turn runs the default long ``bash`` call, which records its
    pid in ``home / "tool.pid"``.

    The session works in ``project`` (``home`` when it is not given). A test of Auto gives one:
    Auto protects the agent's own home folder, so a session whose folder is that home asks about
    every write. ``meta`` is the ``_meta`` of ``session/new``, the way the pager sends its flags.
    """
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
            conn.new_session(cwd=str(project or home), mcp_servers=[], **(meta or {})),
            WIRE_TIMEOUT,
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


async def test_always_approve_mode_never_asks(tmp_path):
    # This client would refuse, if it were asked.
    client, result, marker = await run_write_prompt(
        tmp_path, cancelled, permission="always-approve"
    )

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
    assert listed.sessions == []  # nobody typed in the session, so it has no file to list


# --- the permission mode over the wire -----------------------------------------------------------
#
# Auto runs what its rules can show to be safe and asks about the rest. It protects the agent's own
# home folder, so these tests give the session a project folder that is not the home.

needs_bash = pytest.mark.skipif(bool(shell_problems()), reason="the Bash tool does not run bash")


def mode_folders(tmp_path: Path) -> tuple[Path, Path]:
    """A home folder for the agent and a project folder for the session."""
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return home, project


def bash_call(command: str) -> dict[str, Any]:
    return {"id": CALL_ID, "name": "bash", "arguments": {"command": command}}


def mode_value(options: list[Any]) -> str | None:
    """The current value of the ``mode`` option in the ``config_options`` of a message."""
    return next((option.current_value for option in options if option.id == "mode"), None)


async def prompt_once(conn: ClientSideConnection, session_id: str) -> Any:
    return await asyncio.wait_for(
        conn.prompt(session_id=session_id, prompt=[text_block("go")]), WIRE_TIMEOUT
    )


async def until(condition: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), WIRE_TIMEOUT)


async def test_the_pagers_auto_flag_makes_a_project_write_run_without_a_question(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(cancelled, marker)  # it would refuse, if it were asked
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=write_call(marker),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        result = await prompt_once(conn, session_id)

    assert client.requests == []
    assert marker.read_text(encoding="utf-8") == "the tool ran"
    assert result.stop_reason == "end_turn"


async def test_auto_asks_about_a_write_to_the_agents_own_home(tmp_path):
    home, project = mode_folders(tmp_path)
    target = home / "agent.toml"
    client = ToolClient(reject_once, target)
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=write_call(target),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        result = await prompt_once(conn, session_id)

    ((_session, tool_call, _options),) = client.requests
    assert tool_call.title.startswith("write (Auto: ")
    assert not target.exists()
    assert result.stop_reason == "end_turn"


@needs_bash
async def test_auto_asks_about_a_bash_command_that_its_rules_cannot_show_to_be_safe(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "ran.txt"
    client = ToolClient(reject_once, marker)
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=bash_call("echo ran > ran.txt"),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        result = await prompt_once(conn, session_id)

    ((_session, tool_call, options),) = client.requests
    assert tool_call.tool_call_id == CALL_ID
    assert tool_call.title.startswith("bash (Auto: ")
    assert tool_call.title.endswith(")")
    assert [o.option_id for o in options] == ["allow-once", "reject-once"]
    assert client.tool_ran_before_request == [False]
    assert not marker.exists()
    assert result.stop_reason == "end_turn"


@needs_bash
async def test_a_bash_command_that_auto_asked_about_runs_when_the_user_allows_it(tmp_path):
    home, project = mode_folders(tmp_path)
    client = ToolClient(allow_once)
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=bash_call("echo ran > ran.txt"),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        result = await prompt_once(conn, session_id)

    assert len(client.requests) == 1
    assert (project / "ran.txt").read_text(encoding="utf-8").strip() == "ran"
    assert result.stop_reason == "end_turn"


@needs_bash
async def test_auto_runs_a_bash_command_that_only_reads_without_a_question(tmp_path):
    home, project = mode_folders(tmp_path)
    client = ToolClient(cancelled)
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=bash_call("echo hello"),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        result = await prompt_once(conn, session_id)

    assert client.requests == []
    finished = [
        update
        for update in client.updates
        if getattr(update, "session_update", "") == "tool_call_update"
        and getattr(update, "status", None) == "completed"
    ]
    assert finished, "the call should have run to its end"
    assert result.stop_reason == "end_turn"


@needs_bash
@pytest.mark.parametrize(
    ("start", "meta", "asked"),
    [
        ("ask", {}, True),
        ("ask", {"autoMode": False, "yoloMode": False}, True),
        ("ask", {"yoloMode": True}, False),
        ("ask", {"yoloMode": True, "autoMode": True}, False),
        ("always-approve", {"autoMode": False, "yoloMode": False}, False),
    ],
    ids=["no-flags", "pager-normal", "yolo", "yolo-wins-over-auto", "normal-keeps-agent-toml"],
)
async def test_the_pagers_flags_choose_the_mode_of_a_new_session(tmp_path, start, meta, asked):
    home, project = mode_folders(tmp_path)
    client = ToolClient(reject_once)
    async with tool_agent(
        home,
        client,
        permission=start,
        project=project,
        call=bash_call("echo ran > ran.txt"),
        meta=meta,
    ) as (conn, _process, session_id):
        await prompt_once(conn, session_id)

    assert bool(client.requests) is asked
    assert (project / "ran.txt").exists() is (not asked)


async def test_a_new_session_reports_its_mode_as_a_config_option_and_as_modes(tmp_path):
    home, project = mode_folders(tmp_path)
    client = ToolClient(cancelled)
    async with tool_agent(home, client, permission="ask", project=project) as (conn, _p, _id):
        plain = await asyncio.wait_for(
            conn.new_session(cwd=str(project), mcp_servers=[]), WIRE_TIMEOUT
        )
        auto = await asyncio.wait_for(
            conn.new_session(cwd=str(project), mcp_servers=[], autoMode=True), WIRE_TIMEOUT
        )

    assert mode_value(plain.config_options) == "ask"
    assert plain.modes.current_mode_id == "ask"
    assert mode_value(auto.config_options) == "auto"
    assert auto.modes.current_mode_id == "auto"
    assert [mode.id for mode in auto.modes.available_modes] == ["ask", "auto"]
    (option,) = [o for o in auto.config_options if o.id == "mode"]
    assert option.category == "mode"
    assert [choice.value for choice in option.options] == ["ask", "auto"]


async def test_set_config_option_changes_the_mode_and_the_answer_says_so(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(cancelled, marker)
    async with tool_agent(
        home, client, permission="ask", project=project, call=write_call(marker)
    ) as (conn, _process, session_id):
        answer = await asyncio.wait_for(
            conn.set_config_option(config_id="mode", session_id=session_id, value="auto"),
            WIRE_TIMEOUT,
        )
        assert mode_value(answer.config_options) == "auto"
        result = await prompt_once(conn, session_id)

    assert client.requests == []
    assert marker.read_text(encoding="utf-8") == "the tool ran"
    assert result.stop_reason == "end_turn"


async def test_set_config_option_back_to_ask_puts_the_next_write_to_the_client(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(reject_once, marker)
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=write_call(marker),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        answer = await asyncio.wait_for(
            conn.set_config_option(config_id="mode", session_id=session_id, value="ask"),
            WIRE_TIMEOUT,
        )
        assert mode_value(answer.config_options) == "ask"
        await prompt_once(conn, session_id)

    assert len(client.requests) == 1
    assert not marker.exists()


async def test_set_mode_changes_the_mode_for_a_client_that_reads_modes(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(cancelled, marker)
    async with tool_agent(
        home, client, permission="ask", project=project, call=write_call(marker)
    ) as (conn, _process, session_id):
        await asyncio.wait_for(
            conn.set_session_mode(session_id=session_id, mode_id="auto"), WIRE_TIMEOUT
        )
        await prompt_once(conn, session_id)

    assert client.requests == []
    assert marker.exists()


@pytest.mark.parametrize("mode_id", ["always-approve", "plan", "", "AUTO"])
async def test_a_mode_that_a_client_cannot_choose_is_refused_as_invalid_params(tmp_path, mode_id):
    home, project = mode_folders(tmp_path)
    client = ToolClient(cancelled)
    async with tool_agent(home, client, permission="ask", project=project) as (
        conn,
        _process,
        session_id,
    ):
        with pytest.raises(RequestError) as by_mode:
            await asyncio.wait_for(
                conn.set_session_mode(session_id=session_id, mode_id=mode_id), WIRE_TIMEOUT
            )
        with pytest.raises(RequestError) as by_option:
            await asyncio.wait_for(
                conn.set_config_option(config_id="mode", session_id=session_id, value=mode_id),
                WIRE_TIMEOUT,
            )
        # Still answering, and still in the mode it was in.
        answer = await asyncio.wait_for(
            conn.set_config_option(config_id="mode", session_id=session_id, value="ask"),
            WIRE_TIMEOUT,
        )

    assert by_mode.value.code == -32602
    assert by_option.value.code == -32602
    assert mode_value(answer.config_options) == "ask"


async def test_the_pagers_mode_notification_moves_the_open_session_and_tells_the_client(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(cancelled, marker)
    async with tool_agent(
        home, client, permission="ask", project=project, call=write_call(marker)
    ) as (conn, _process, session_id):
        await conn.ext_notification("pi/yolo_mode_changed", {"permission_mode": "auto"})
        await asyncio.wait_for(client.mode_pushed.wait(), WIRE_TIMEOUT)
        pushed = [
            update
            for update in client.updates
            if getattr(update, "session_update", "") == "config_option_update"
        ]
        assert mode_value(pushed[-1].config_options) == "auto"
        result = await prompt_once(conn, session_id)

    assert client.requests == []
    assert marker.read_text(encoding="utf-8") == "the tool ran"
    assert result.stop_reason == "end_turn"


async def test_a_mode_notification_with_a_value_that_is_not_a_mode_changes_nothing(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(reject_once, marker)
    async with tool_agent(
        home, client, permission="ask", project=project, call=write_call(marker)
    ) as (conn, _process, session_id):
        await conn.ext_notification("pi/yolo_mode_changed", {"permission_mode": "plan"})
        await conn.ext_notification("pi/yolo_mode_changed", {})
        await prompt_once(conn, session_id)

    assert len(client.requests) == 1
    assert not client.mode_pushed.is_set()


async def test_entering_auto_tells_the_user_what_auto_does_not_protect(tmp_path):
    home, project = mode_folders(tmp_path)
    client = ToolClient(cancelled)
    async with tool_agent(home, client, permission="ask", project=project) as (
        conn,
        _process,
        session_id,
    ):
        await conn.set_config_option(config_id="mode", session_id=session_id, value="auto")
        await until(lambda: ENTRY_MESSAGE in client.agent_text())

    text = client.agent_text()
    assert "fetch_url" in text
    assert "A sandbox is the boundary" in text


async def test_the_decisions_of_auto_are_in_the_session_file_without_the_content(tmp_path):
    home, project = mode_folders(tmp_path)
    marker = project / "marker.txt"
    client = ToolClient(cancelled, marker)
    async with tool_agent(
        home,
        client,
        permission="ask",
        project=project,
        call=write_call(marker),
        meta={"autoMode": True},
    ) as (conn, _process, session_id):
        await prompt_once(conn, session_id)

    entries = [
        json.loads(line)
        for path in (home / "sessions").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decisions = [e for e in entries if e.get("customType") == AUDIT_ENTRY_TYPE]
    assert len(decisions) == 1
    (record,) = decisions
    assert record["data"]["tool"] == "write"
    assert record["data"]["outcome"] == "allow"
    assert "the tool ran" not in json.dumps(record)


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
    async with tool_agent(tmp_path, client, permission="always-approve") as (
        conn,
        process,
        session_id,
    ):
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
