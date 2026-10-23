"""``Manager`` and ``ServerConnection`` against real MCP servers (stdio child processes)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest
from mcp_support import (
    MOCK_TOOLS,
    Lab,
    Signal,
    kill_pid,
    python_server,
    read_pid,
    stdio_server,
    text_of,
    until,
    wait_gone,
)
from pi_mcp import manager as manager_module
from pi_mcp.errors import ConnectionUnavailable
from pi_mcp.settings import Settings

# ---------------------------------------------------------------------------
# Connecting and registering
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_each_tool_of_the_server_is_registered_with_a_prefixed_name(self, lab: Lab):
        await lab.start(stdio_server())

        assert set(lab.pi.tools) == {f"mcp__mock__{name}" for name in MOCK_TOOLS}

    async def test_a_tool_has_its_schema_description_and_label(self, lab: Lab):
        await lab.start(stdio_server())

        tool = lab.pi.tools["mcp__mock__add"]
        assert set(tool.parameters["properties"]) == {"a", "b"}
        assert tool.description == "Add two numbers."
        assert tool.label == "mock: add"

    async def test_the_annotations_of_the_server_are_kept(self, lab: Lab):
        await lab.start(stdio_server())

        assert lab.pi.tools["mcp__mock__readonly"].annotations == {"readOnlyHint": True}
        assert lab.pi.tools["mcp__mock__echo"].annotations is None

    async def test_a_tool_called_like_a_builtin_does_not_take_its_place(self, lab: Lab):
        await lab.start(stdio_server())

        assert "bash" not in lab.pi.tools
        assert "mcp__mock__bash" in lab.pi.tools
        assert await lab.text("mcp__mock__bash") == "not the real bash"

    async def test_nothing_is_said_when_all_is_well(self, lab: Lab):
        manager = await lab.start(stdio_server())

        assert lab.pi.notices == []
        assert [c.state for c in manager.connections.values()] == ["ready"]

    async def test_two_servers_start_side_by_side(self, lab: Lab):
        await lab.start(stdio_server("one"), stdio_server("two"))

        assert {"mcp__one__echo", "mcp__two__echo"} <= set(lab.pi.tools)

    async def test_start_returns_before_any_server_has_answered(self, lab: Lab):
        manager = await lab.start(stdio_server(), wait=False)

        assert manager.connections["mock"].state == "starting"
        assert lab.pi.tools == {}
        await lab.settled(manager)
        assert lab.pi.tools

    async def test_the_server_runs_in_the_directory_of_the_session(self, lab: Lab):
        await lab.start(stdio_server())

        assert os.path.samefile(await lab.text("mcp__mock__cwd"), lab.tmp_path)

    async def test_the_same_server_name_twice_uses_the_first_and_says_so(self, lab: Lab):
        manager = await lab.start(stdio_server("same"), stdio_server("same", command="no-such-cmd"))

        assert list(manager.connections) == ["same"]
        assert manager.connections["same"].state == "ready"
        assert any('"same"' in n and "twice" in n for n in lab.pi.notices)


# ---------------------------------------------------------------------------
# Calling tools
# ---------------------------------------------------------------------------


class TestCalls:
    async def test_a_text_result(self, lab: Lab):
        await lab.start(stdio_server())

        result = await lab.call("mcp__mock__echo", {"text": "hi"})

        assert text_of(result) == "echo: hi"
        assert result.details == {"mcp": {"server": "mock", "tool": "echo"}}

    async def test_arguments_reach_the_server(self, lab: Lab):
        await lab.start(stdio_server())

        assert await lab.text("mcp__mock__add", {"a": 2, "b": 40}) == "42"

    async def test_an_error_result_raises_with_the_text_of_the_server(self, lab: Lab):
        await lab.start(stdio_server())

        with pytest.raises(RuntimeError, match="boom"):
            await lab.call("mcp__mock__fail", {"message": "boom"})

    async def test_an_error_does_not_end_the_server(self, lab: Lab):
        manager = await lab.start(stdio_server())
        with pytest.raises(RuntimeError):
            await lab.call("mcp__mock__fail", {"message": "boom"})

        assert await lab.text("mcp__mock__echo", {"text": "again"}) == "echo: again"
        assert manager.connections["mock"].state == "ready"

    async def test_arguments_that_the_server_refuses_raise_its_message(self, lab: Lab):
        await lab.start(stdio_server())

        with pytest.raises(RuntimeError, match="validation error"):
            await lab.call("mcp__mock__add", {"a": "not a number", "b": 1})

    async def test_a_result_with_only_structured_content_becomes_json_text(self, lab: Lab):
        await lab.start(stdio_server())

        text = await lab.text("mcp__mock__structured")

        assert '"answer": 42' in text and '"items": ["a", "b"]' in text

    async def test_a_big_result_is_cut_and_the_note_says_so(self, lab: Lab):
        await lab.start(stdio_server())

        result = await lab.call("mcp__mock__big", {"size": 200_000})

        first, note = result.content
        assert first["text"] == "x" * (50 * 1024)
        assert note["text"].startswith("[Output cut: the first 50.0KB of 195.3KB are shown.]")

    async def test_an_image_is_passed_on(self, lab: Lab):
        await lab.start(stdio_server())

        result = await lab.call("mcp__mock__image")

        assert [b["type"] for b in result.content] == ["image"]

    async def test_calls_to_one_server_run_side_by_side(self, lab: Lab):
        await lab.start(stdio_server())
        started = time.monotonic()

        await asyncio.gather(
            lab.call("mcp__mock__slow", {"seconds": 1.0}),
            lab.call("mcp__mock__slow", {"seconds": 1.0}),
        )

        assert time.monotonic() - started < 1.8

    async def test_a_call_that_takes_too_long_ends_and_the_server_stays_usable(self, lab: Lab):
        manager = await lab.start(stdio_server(), settings=Settings(30.0, 0.5))

        with pytest.raises(RuntimeError, match=r'did not answer the call of "slow" within 0.5 s'):
            await lab.call("mcp__mock__slow", {"seconds": 20})

        assert await lab.text("mcp__mock__echo", {"text": "x"}) == "echo: x"
        assert manager.connections["mock"].state == "ready"

    async def test_aborting_ends_a_call_at_once(self, lab: Lab):
        await lab.start(stdio_server())
        stop = Signal()
        call = asyncio.ensure_future(lab.call("mcp__mock__slow", {"seconds": 30}, signal=stop))
        await asyncio.sleep(0.5)

        stop.abort()

        with pytest.raises(RuntimeError, match="Operation aborted"):
            await asyncio.wait_for(call, 5)
        assert await lab.text("mcp__mock__echo", {"text": "x"}) == "echo: x"

    async def test_a_signal_that_is_already_aborted_sends_nothing(self, lab: Lab):
        manager = await lab.start(stdio_server())
        stop = Signal()
        stop.abort()
        server_calls: list[str] = []
        original = manager.connections["mock"]._invoke

        async def spy(*args: Any) -> Any:
            server_calls.append("call")
            return await original(*args)

        manager.connections["mock"]._invoke = spy  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="Operation aborted"):
            await lab.call("mcp__mock__echo", {"text": "x"}, signal=stop)

        assert server_calls == []

    async def test_cancelling_the_caller_leaves_the_server_usable(self, lab: Lab):
        manager = await lab.start(stdio_server())
        call = asyncio.ensure_future(lab.call("mcp__mock__slow", {"seconds": 30}))
        await asyncio.sleep(0.5)

        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        assert await lab.text("mcp__mock__echo", {"text": "x"}) == "echo: x"
        assert manager.connections["mock"].state == "ready"


# ---------------------------------------------------------------------------
# A server that does not start
# ---------------------------------------------------------------------------


class TestFailedStart:
    async def test_a_missing_command_is_reported_once_and_has_no_tools(self, lab: Lab):
        manager = await lab.start(stdio_server("ghost", command="no-such-command-pi-mcp"))

        connection = manager.connections["ghost"]
        assert connection.state == "failed"
        assert (
            connection.error
            == 'command not found: "no-such-command-pi-mcp". Check that it is on PATH.'
        )
        assert lab.pi.tools == {}
        [notice] = lab.pi.notices
        assert notice.startswith('MCP server "ghost" did not start: command not found')
        assert "Its tools are not available in this session." in notice

    async def test_a_server_that_ends_at_once_is_reported_and_its_stderr_is_in_the_log(
        self, lab: Lab
    ):
        manager = await lab.start(stdio_server(env=(("PI_MCP_TEST_EXIT", "1"),)))

        assert manager.connections["mock"].state == "failed"
        [notice] = lab.pi.notices
        assert 'MCP server "mock" did not start:' in notice
        assert str(lab.pi.home / "logs" / "mcp" / "mock.log") in notice
        assert "refusing to start" in lab.log()

    async def test_a_server_that_does_not_answer_is_ended_at_the_start_up_limit(
        self, lab: Lab, tmp_path: Path
    ):
        pid_file = tmp_path / "slow.pid"
        server = stdio_server(
            env=(("PI_MCP_TEST_START_DELAY", "120"), ("PI_MCP_TEST_PID_FILE", str(pid_file)))
        )
        started = time.monotonic()

        manager = await lab.start(server, settings=Settings(1.0, 30.0))

        connection = manager.connections["mock"]
        assert connection.state == "failed"
        assert connection.error == "it did not answer within 1 s"
        assert time.monotonic() - started < 15
        await wait_gone(await read_pid(pid_file))  # the child does not stay behind
        assert "did not answer within 1 s" in lab.pi.notices[0]

    async def test_one_server_that_fails_does_not_stop_the_others(self, lab: Lab):
        manager = await lab.start(stdio_server("good"), stdio_server("bad", command="no-such-cmd"))

        assert manager.connections["good"].state == "ready"
        assert manager.connections["bad"].state == "failed"
        assert await lab.text("mcp__good__echo", {"text": "x"}) == "echo: x"

    async def test_a_server_that_did_not_come_back_is_tried_again_by_the_next_call(
        self, lab: Lab, tmp_path: Path
    ):
        need = tmp_path / "need.flag"
        need.write_text("here")
        manager = await lab.start(stdio_server(env=(("PI_MCP_TEST_NEED_FILE", str(need)),)))
        need.unlink()
        await manager.command("reconnect mock")  # the server does not start any more
        assert manager.connections["mock"].state == "failed"
        need.write_text("here")  # the cause is gone

        assert await lab.text("mcp__mock__echo", {"text": "x"}) == "echo: x"

        assert manager.connections["mock"].state == "ready"

    async def test_a_failed_server_can_be_connected_again_when_the_cause_is_gone(
        self, lab: Lab, tmp_path: Path
    ):
        need = tmp_path / "need.flag"
        manager = await lab.start(stdio_server(env=(("PI_MCP_TEST_NEED_FILE", str(need)),)))
        assert manager.connections["mock"].state == "failed"

        need.write_text("here")
        answer = await manager.command("reconnect mock")

        assert answer.startswith("- mock: stdio (")
        assert f"ready, {len(MOCK_TOOLS)} tools" in answer
        assert await lab.text("mcp__mock__echo", {"text": "x"}) == "echo: x"

    async def test_a_second_failure_of_the_same_server_is_not_told_again(self, lab: Lab):
        manager = await lab.start(stdio_server("ghost", command="no-such-command-pi-mcp"))

        answer = await manager.command("reconnect ghost")

        assert "failed" in answer
        assert len(lab.pi.notices) == 1

    async def test_the_error_of_the_server_does_not_show_a_secret(self, lab: Lab):
        code = (
            "import json, sys\n"
            "request = json.loads(sys.stdin.readline())\n"
            "reply = {'jsonrpc': '2.0', 'id': request['id'],\n"
            "         'error': {'code': -32000, 'message': 'bad key sk-secret-key-123 given'}}\n"
            "print(json.dumps(reply), flush=True)\n"
            "sys.stdin.read()\n"
        )
        manager = await lab.start(
            python_server("evil", code, env=(("API_KEY", "sk-secret-key-123"),))
        )

        connection = manager.connections["evil"]
        assert connection.state == "failed"
        assert "bad key *** given" in connection.error
        assert "sk-secret-key-123" not in " ".join(lab.pi.notices)
        assert "sk-secret-key-123" not in await manager.status()


# ---------------------------------------------------------------------------
# A server that stops while the session runs
# ---------------------------------------------------------------------------


class TestWhileRunning:
    async def test_a_server_that_ends_during_a_call_is_reported_to_the_model(self, lab: Lab):
        manager = await lab.start(stdio_server())

        with pytest.raises(RuntimeError, match=r'"mock" stopped while the tool ran'):
            await lab.call("mcp__mock__crash")

        assert manager.connections["mock"].state == "dead"

    async def test_the_next_call_connects_the_server_again(self, lab: Lab):
        manager = await lab.start(stdio_server())
        first = await lab.server_pid()
        with pytest.raises(RuntimeError):
            await lab.call("mcp__mock__crash")

        assert await lab.text("mcp__mock__echo", {"text": "back"}) == "echo: back"

        assert manager.connections["mock"].state == "ready"
        assert await lab.server_pid() != first
        # Every tool once, and under the same name as before (a repeat would be "..._2").
        assert sorted(lab.pi.register_calls) == sorted(f"mcp__mock__{t}" for t in MOCK_TOOLS)

    async def test_a_request_that_was_not_sent_goes_out_again_after_the_reconnect(self, lab: Lab):
        manager = await lab.start(stdio_server())
        first = await lab.server_pid()
        kill_pid(first)
        await wait_gone(first)
        await asyncio.sleep(1.0)  # the SDK sees the end of the pipe

        assert await lab.text("mcp__mock__echo", {"text": "again"}) == "echo: again"

        assert manager.connections["mock"].state == "ready"
        assert await lab.server_pid() != first

    async def test_calls_that_wait_for_a_reconnect_make_one_and_not_one_each(self, lab: Lab):
        await lab.start(stdio_server())
        first = await lab.server_pid()
        kill_pid(first)
        await wait_gone(first)
        await asyncio.sleep(1.0)

        answers = await asyncio.gather(
            *(lab.text("mcp__mock__echo", {"text": str(i)}) for i in range(3))
        )

        assert answers == ["echo: 0", "echo: 1", "echo: 2"]
        assert lab.log().count(" start ---") == 2  # the first start, and one reconnect

    async def test_a_server_that_keeps_dying_is_not_started_again_and_again(self, lab: Lab):
        manager = await lab.start(stdio_server())
        # The first call ends the server; each of the next three connects it first.
        for _ in range(4):
            with pytest.raises(RuntimeError, match="stopped while the tool ran"):
                await lab.call("mcp__mock__crash")

        with pytest.raises(
            ConnectionUnavailable, match=r"cannot reconnect by itself\. Use /mcp reconnect mock"
        ):
            await lab.call("mcp__mock__echo", {"text": "no"})

        assert [n for n in lab.pi.notices if "cannot reconnect by itself" in n]
        answer = await manager.command("reconnect mock")  # the user can always
        assert "ready" in answer
        assert await lab.text("mcp__mock__echo", {"text": "yes"}) == "echo: yes"

    async def test_calls_that_work_give_the_reconnects_back(self, lab: Lab):
        manager = await lab.start(stdio_server())
        for _ in range(4):  # more than the limit of three: each stop after a good call is new
            with pytest.raises(RuntimeError, match="stopped while the tool ran"):
                await lab.call("mcp__mock__crash")
            assert await lab.text("mcp__mock__echo", {"text": "ok"}) == "echo: ok"

        assert manager.connections["mock"].state == "ready"
        assert [n for n in lab.pi.notices if "cannot reconnect" in n] == []

    async def test_a_reconnect_by_the_user_gives_the_calls_their_reconnects_back(self, lab: Lab):
        manager = await lab.start(stdio_server())
        manager.connections["mock"]._auto_reconnects = 3  # all used
        await manager.command("reconnect mock")

        with pytest.raises(RuntimeError, match="stopped while the tool ran"):
            await lab.call("mcp__mock__crash")

        assert await lab.text("mcp__mock__echo", {"text": "ok"}) == "echo: ok"

    async def test_a_server_that_cannot_come_back_tells_the_model(self, lab: Lab, tmp_path: Path):
        need = tmp_path / "need.flag"
        need.write_text("here")
        manager = await lab.start(stdio_server(env=(("PI_MCP_TEST_NEED_FILE", str(need)),)))
        with pytest.raises(RuntimeError):
            await lab.call("mcp__mock__crash")
        need.unlink()

        with pytest.raises(
            ConnectionUnavailable, match=r"stopped and did not come back: .*Use /mcp reconnect mock"
        ):
            await lab.call("mcp__mock__echo", {"text": "x"})

        assert manager.connections["mock"].state == "failed"

    async def test_a_call_that_waits_does_not_wait_for_the_time_limit_when_the_connection_ends(
        self, lab: Lab
    ):
        manager = await lab.start(stdio_server())  # the time limit of a call is 30 s here
        call = asyncio.ensure_future(lab.call("mcp__mock__slow", {"seconds": 25}))
        await asyncio.sleep(0.5)
        started = time.monotonic()

        manager.connections["mock"].mark_dead("the test ended it")  # the task ends the connection

        with pytest.raises(RuntimeError, match=r'"mock" stopped while the tool ran'):
            await asyncio.wait_for(call, 15)
        assert time.monotonic() - started < 10

    async def test_status_finds_a_server_that_is_gone(self, lab: Lab):
        manager = await lab.start(stdio_server())
        pid = await lab.server_pid()
        kill_pid(pid)
        await wait_gone(pid)
        await asyncio.sleep(1.0)

        status = await manager.status()

        assert manager.connections["mock"].state == "dead"
        assert "- mock: stdio (" in status and "stopped:" in status
        assert "The next tool call connects it again" in status

    async def test_status_does_not_call_a_busy_server_dead(self, lab: Lab):
        manager = await lab.start(stdio_server())
        busy = asyncio.ensure_future(lab.call("mcp__mock__slow", {"seconds": 2}))
        await asyncio.sleep(0.3)

        status = await manager.status()

        assert f"ready, {len(MOCK_TOOLS)} tools" in status
        assert manager.connections["mock"].state == "ready"
        await busy

    async def test_reconnect_restarts_a_server_that_is_fine(self, lab: Lab):
        manager = await lab.start(stdio_server())
        first = await lab.server_pid()

        answer = await manager.command("reconnect mock")

        assert "ready" in answer
        assert await lab.server_pid() != first
        await wait_gone(first)  # the old process did not stay behind


# ---------------------------------------------------------------------------
# The end of the session
# ---------------------------------------------------------------------------


class TestClose:
    async def test_closing_ends_the_server_process(self, lab: Lab):
        manager = await lab.start(stdio_server())
        pid = await lab.server_pid()

        await manager.close()

        await wait_gone(pid)
        assert manager.connections["mock"].state == "closed"
        assert not [t for t in asyncio.all_tasks() if (t.get_name() or "").startswith("pi-mcp:")]

    async def test_closing_while_a_server_still_starts_ends_it_too(self, lab: Lab, tmp_path: Path):
        pid_file = tmp_path / "late.pid"
        manager = await lab.start(
            stdio_server(
                env=(("PI_MCP_TEST_START_DELAY", "120"), ("PI_MCP_TEST_PID_FILE", str(pid_file)))
            ),
            wait=False,
        )
        pid = await read_pid(pid_file)
        started = time.monotonic()

        await manager.close()

        assert time.monotonic() - started < 20
        await wait_gone(pid)
        assert manager.connections["mock"].state == "closed"

    async def test_closing_twice_does_no_harm(self, lab: Lab):
        manager = await lab.start(stdio_server())

        await manager.close()
        await manager.close()

        assert manager.connections["mock"].state == "closed"

    async def test_a_call_after_the_end_is_refused(self, lab: Lab):
        manager = await lab.start(stdio_server())
        await manager.close()

        with pytest.raises(ConnectionUnavailable, match="is closed"):
            await lab.call("mcp__mock__echo", {"text": "x"})

    async def test_closing_at_once_after_the_start_leaves_nothing_behind(self, lab: Lab):
        manager = await lab.start(stdio_server(), wait=False)
        await manager.close()  # at once: the task may be anywhere in its start

        assert manager.connections["mock"].state == "closed"
        assert not [t for t in asyncio.all_tasks() if (t.get_name() or "").startswith("pi-mcp:")]


# ---------------------------------------------------------------------------
# What a server gets and where its words go
# ---------------------------------------------------------------------------


class TestEnvironment:
    async def test_the_llm_key_of_this_process_does_not_reach_the_server(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REAL_LLM_API_KEY", "sk-llm-key-must-not-leak")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-leak")
        await lab.start(stdio_server())

        names = (await lab.text("mcp__mock__env_names")).split("\n")

        assert "REAL_LLM_API_KEY" not in names and "OPENAI_API_KEY" not in names
        assert await lab.text("mcp__mock__env_value", {"name": "REAL_LLM_API_KEY"}) == "<unset>"

    async def test_the_env_of_the_client_arrives_as_it_is_without_expansion(self, lab: Lab):
        await lab.start(stdio_server(env=(("MY_TOKEN", "${HOME}/tok"), ("PLAIN", "abc"))))

        assert await lab.text("mcp__mock__env_value", {"name": "MY_TOKEN"}) == "${HOME}/tok"
        assert await lab.text("mcp__mock__env_value", {"name": "PLAIN"}) == "abc"

    async def test_proxy_variables_of_this_process_are_passed_on(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        await lab.start(stdio_server())

        assert (
            await lab.text("mcp__mock__env_value", {"name": "HTTPS_PROXY"})
            == "http://proxy.invalid:3128"
        )

    async def test_what_the_server_writes_to_stderr_goes_to_its_log(self, lab: Lab):
        await lab.start(stdio_server())

        await lab.call("mcp__mock__noisy")

        await until(lambda: "a line on stderr" in lab.log())


class TestSecretsInTexts:
    async def test_a_token_in_an_error_of_the_tool_is_masked(self, lab: Lab):
        token = "tok-abcdef-123456"
        await lab.start(stdio_server(env=(("API_TOKEN", token),)))

        with pytest.raises(RuntimeError) as caught:
            await lab.call("mcp__mock__fail", {"message": f"the token {token} was refused"})

        assert token not in str(caught.value)
        assert "the token *** was refused" in str(caught.value)


# ---------------------------------------------------------------------------
# The turn barrier
# ---------------------------------------------------------------------------


class TestBarrier:
    @staticmethod
    def slow_server(tmp_path: Path, seconds: str) -> Any:
        return stdio_server(
            env=(
                ("PI_MCP_TEST_START_DELAY", seconds),
                ("PI_MCP_TEST_PID_FILE", str(tmp_path / "s.pid")),
            )
        )

    async def test_it_waits_for_a_server_that_starts_quickly(self, lab: Lab):
        manager = await lab.start(stdio_server(), wait=False)

        await manager.wait_started()

        assert manager.connections["mock"].state == "ready"
        assert "mcp__mock__echo" in lab.pi.tools
        assert lab.pi.notices == []

    async def test_it_returns_at_once_when_nothing_is_starting(self, lab: Lab):
        manager = await lab.start(stdio_server())

        await asyncio.wait_for(manager.wait_started(), 0.5)

    async def test_it_gives_up_on_a_slow_server_and_says_so_once(
        self, lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(manager_module, "BARRIER_WAIT", 0.3)
        manager = await lab.start(self.slow_server(tmp_path, "2"), wait=False)

        await manager.wait_started()
        assert manager.connections["mock"].state == "starting"
        assert lab.pi.notices == [
            'MCP server "mock" is still starting. Its tools join in a later turn.'
        ]

        started = time.monotonic()
        await manager.wait_started()  # a later turn: no waiting again
        assert time.monotonic() - started < 0.2
        assert len(lab.pi.notices) == 1

        await lab.settled(manager)  # the server goes on, and its tools join
        assert "mcp__mock__echo" in lab.pi.tools

    async def test_cancelling_the_wait_does_not_stop_the_server(self, lab: Lab, tmp_path: Path):
        manager = await lab.start(self.slow_server(tmp_path, "2"), wait=False)
        waiting = asyncio.ensure_future(manager.wait_started())
        await asyncio.sleep(0.3)

        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

        await lab.settled(manager)
        assert manager.connections["mock"].state == "ready"
