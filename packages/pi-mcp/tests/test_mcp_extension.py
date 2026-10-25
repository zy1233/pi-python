"""The extension as the harness loads it: claim, start, turn barrier, tools in a turn, /mcp, end."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pi_mcp
import pytest
from mcp_support import (
    MOCK_SERVER,
    MOCK_TOOLS,
    ToolScript,
    block_text,
    python_server,
    read_pid,
    stdio_server,
    until,
    wait_gone,
)
from pi_mcp import manager as manager_module
from pi_mcp.manager import NO_SERVERS

from pi_agent_core.extensions import ClientMcpServer
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import Model
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

WAIT = 30


class Rig:
    """Harnesses with the extension loaded. All of them are closed at the end of the test."""

    def __init__(self) -> None:
        self.harnesses: list[AgentHarness] = []
        self.events: list[Any] = []
        self.event_times: list[float] = []

    async def harness(
        self, *servers: ClientMcpServer, llm: Any = None, load: bool = True
    ) -> AgentHarness:
        harness = AgentHarness(
            session=Session(
                await MemorySessionStorage.create(session_id=f"mcp-{len(self.harnesses)}")
            ),
            model=Model(provider="mock", model_id="m1"),
            stream_fn=llm or mock_text_stream,
            extensions=[pi_mcp.activate],
        )
        harness.set_client_mcp_servers(list(servers))
        self.harnesses.append(harness)

        async def on_event(event: Any, signal: Any = None) -> None:
            self.events.append(event)
            self.event_times.append(time.monotonic())

        harness.subscribe(on_event)
        if load:
            await harness.load_extensions()
        return harness

    def notices(self) -> list[str]:
        return [event.text for event in self.events if getattr(event, "type", None) == "notice"]

    def times_of(self, event_type: str) -> list[float]:
        return [
            at
            for event, at in zip(self.events, self.event_times, strict=True)
            if getattr(event, "type", None) == event_type
        ]

    async def aclose(self) -> None:
        for harness in self.harnesses:
            await harness.close()


@pytest.fixture()
async def rig() -> AsyncIterator[Rig]:
    laboratory = Rig()
    try:
        yield laboratory
    finally:
        await laboratory.aclose()


async def results_of_tools(harness: AgentHarness) -> list[str]:
    context = await harness.session.build_context()
    return [
        "".join(block_text(b) for b in message.content)
        for message in context.messages
        if getattr(message, "role", None) == "toolResult"
    ]


def pid_file_env(path: Path, *more: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return (("PI_MCP_TEST_PID_FILE", str(path)), *more)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


class TestLoading:
    async def test_the_servers_are_claimed(self, rig: Rig):
        harness = await rig.harness(stdio_server("a"), stdio_server("b"))

        assert harness.unclaimed_client_mcp_servers == ()

    async def test_without_servers_nothing_is_started_and_the_command_says_so(self, rig: Rig):
        harness = await rig.harness()

        assert await harness.dispatch_command("mcp", "") == NO_SERVERS
        assert rig.notices() == []

    async def test_the_command_is_listed_for_the_client(self, rig: Rig):
        harness = await rig.harness()

        assert "mcp" in harness.extension_registry.get_commands()

    async def test_loading_starts_the_servers_without_waiting_for_them(
        self, rig: Rig, tmp_path: Path
    ):
        pid_file = tmp_path / "s.pid"
        server = stdio_server(env=pid_file_env(pid_file, ("PI_MCP_TEST_START_DELAY", "5")))
        started = time.monotonic()

        await rig.harness(server)

        assert time.monotonic() - started < 4  # the server needs 5 s: loading did not wait
        await read_pid(pid_file)  # but it is on its way

    async def test_a_start_that_raises_does_not_break_the_load_of_the_extensions(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        def broken(self: Any) -> None:
            raise RuntimeError("cannot start")

        monkeypatch.setattr(manager_module.Manager, "start", broken)

        with caplog.at_level(logging.WARNING, logger="pi_mcp"):
            harness = await rig.harness(stdio_server())

        assert "could not start the MCP servers" in caplog.text
        assert "mcp" in harness.extension_registry.get_commands()


class TestOlderCore:
    def test_a_core_that_does_not_hand_over_the_servers_is_left_alone(
        self, caplog: pytest.LogCaptureFixture
    ):
        registered: list[str] = []
        old_api = SimpleNamespace(
            register_command=lambda *a, **k: registered.append("command"),
            register_tool=lambda *a, **k: registered.append("tool"),
            on=lambda *a, **k: registered.append("handler"),
        )  # no ``client_mcp_servers``

        with caplog.at_level(logging.WARNING, logger="pi_mcp"):
            pi_mcp.activate(old_api)  # type: ignore[arg-type]

        assert registered == []
        assert "needs a newer pi-agent-core-lc" in caplog.text

    def test_activate_only_registers_and_starts_nothing(self):
        calls: list[str] = []
        api = SimpleNamespace(
            client_mcp_servers=(stdio_server(),),
            cwd="/tmp",
            home=Path("/tmp/pi-home-not-used"),
            claim_client_mcp_servers=lambda names: calls.append(f"claim {list(names)}"),
            on=lambda event, handler: calls.append(f"on {event}"),
            add_turn_barrier=lambda wait: calls.append("barrier"),
            register_cleanup=lambda callback: calls.append("cleanup"),
            register_command=lambda name, **kwargs: calls.append(f"command {name}"),
        )

        pi_mcp.activate(api)  # type: ignore[arg-type]

        assert calls == [
            "claim ['mock']",
            "on session_start",
            "barrier",
            "cleanup",
            "command mcp",
        ]


# ---------------------------------------------------------------------------
# A turn
# ---------------------------------------------------------------------------


class TestTurns:
    async def test_the_first_turn_offers_the_tools_of_the_servers(self, rig: Rig):
        llm = ToolScript()
        harness = await rig.harness(stdio_server(), llm=llm)

        await asyncio.wait_for(harness.prompt("hello"), WAIT)

        assert {f"mcp__mock__{name}" for name in MOCK_TOOLS} <= set(llm.offered[0])

    async def test_a_tool_call_of_the_model_reaches_the_server(self, rig: Rig):
        llm = ToolScript(("mcp__mock__echo", {"text": "from the model"}))
        harness = await rig.harness(stdio_server(), llm=llm)

        await asyncio.wait_for(harness.prompt("use the tool"), WAIT)

        assert await results_of_tools(harness) == ["echo: from the model"]

    async def test_a_tool_that_fails_gives_the_model_an_error_result_and_the_turn_goes_on(
        self, rig: Rig
    ):
        llm = ToolScript(("mcp__mock__fail", {"message": "boom"}))
        harness = await rig.harness(stdio_server(), llm=llm)

        result = await asyncio.wait_for(harness.prompt("use the tool"), WAIT)

        [text] = await results_of_tools(harness)
        assert "boom" in text
        assert result is not None
        context = await harness.session.build_context()
        [tool_result] = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
        assert getattr(tool_result, "isError", getattr(tool_result, "is_error", None)) is True

    async def test_two_calls_in_one_turn_run_side_by_side(self, rig: Rig):
        llm = ToolScript(
            ("mcp__mock__slow", {"seconds": 1.5}), ("mcp__mock__slow", {"seconds": 1.5})
        )
        harness = await rig.harness(stdio_server(), llm=llm)

        await asyncio.wait_for(harness.prompt("two at once"), WAIT)

        assert await results_of_tools(harness) == ["slow done", "slow done"]
        assert len(rig.times_of("tool_execution_start")) == 2
        assert max(rig.times_of("tool_execution_start")) < min(rig.times_of("tool_execution_end"))

    async def test_aborting_the_turn_ends_a_call_in_flight_and_leaves_the_server_usable(
        self, rig: Rig
    ):
        llm = ToolScript(("mcp__mock__slow", {"seconds": 60}))
        harness = await rig.harness(stdio_server(), llm=llm)
        turn = asyncio.ensure_future(harness.prompt("a long call"))
        await until(lambda: rig.times_of("tool_execution_start"))
        started = time.monotonic()

        await harness.abort()

        await asyncio.wait_for(asyncio.gather(turn, return_exceptions=True), 15)
        assert time.monotonic() - started < 10
        assert "ready" in ((await harness.dispatch_command("mcp", "")) or "")

    async def test_a_server_that_fails_to_start_is_told_once_and_the_turn_goes_on(self, rig: Rig):
        llm = ToolScript()
        harness = await rig.harness(
            stdio_server("ghost", command="no-such-command-pi-mcp"), llm=llm
        )

        result = await asyncio.wait_for(harness.prompt("hello"), WAIT)
        await asyncio.wait_for(harness.prompt("hello again"), WAIT)

        assert result is not None
        await until(lambda: rig.notices())
        [notice] = rig.notices()
        assert notice.startswith('MCP server "ghost" did not start: command not found')
        assert not [name for name in llm.offered[0] if name.startswith("mcp__")]

    async def test_a_slow_server_does_not_hold_the_first_turn_for_ever(
        self, rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(manager_module, "BARRIER_WAIT", 0.5)
        llm = ToolScript()
        server = stdio_server(
            env=pid_file_env(tmp_path / "s.pid", ("PI_MCP_TEST_START_DELAY", "3"))
        )
        harness = await rig.harness(server, llm=llm)

        started = time.monotonic()
        await asyncio.wait_for(harness.prompt("first"), WAIT)
        first_turn = time.monotonic() - started

        assert first_turn < 3
        assert not [name for name in llm.offered[0] if name.startswith("mcp__")]
        await until(lambda: rig.notices())
        assert rig.notices() == [
            'MCP server "mock" is still starting. Its tools join in a later turn.'
        ]

        async def ready() -> bool:
            return "ready" in ((await harness.dispatch_command("mcp", "")) or "")

        deadline = time.monotonic() + WAIT
        while not await ready():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.3)
        await asyncio.wait_for(harness.prompt("second"), WAIT)
        assert "mcp__mock__echo" in llm.offered[-1]
        assert len(rig.notices()) == 1


# ---------------------------------------------------------------------------
# /mcp
# ---------------------------------------------------------------------------


class TestCommand:
    async def test_it_lists_the_servers_and_their_state(self, rig: Rig):
        harness = await rig.harness(stdio_server("one"), stdio_server("two"))
        await asyncio.wait_for(harness.prompt("hello"), WAIT)  # the barrier: both are ready

        text = await harness.dispatch_command("mcp", "")

        lines = (text or "").splitlines()
        assert lines[0] == "MCP servers (2):"
        assert lines[1].startswith("- one: stdio (") and lines[1].endswith(
            f"ready, {len(MOCK_TOOLS)} tools"
        )
        assert lines[2].startswith("- two: stdio (") and lines[2].endswith(
            f"ready, {len(MOCK_TOOLS)} tools"
        )

    async def test_it_shows_no_secret(self, rig: Rig):
        secret = "sk-very-secret-value-1"
        harness = await rig.harness(
            stdio_server(args=(str(MOCK_SERVER), secret), env=(("API_KEY", secret),)),
            python_server("broken", "raise SystemExit(1)", secret, env=(("T", secret),)),
        )
        await asyncio.wait_for(harness.prompt("hello"), WAIT)

        text = await harness.dispatch_command("mcp", "")

        assert secret not in (text or "")
        assert secret not in " ".join(rig.notices())

    async def test_reconnect_starts_the_server_again(self, rig: Rig, tmp_path: Path):
        pid_file = tmp_path / "s.pid"
        harness = await rig.harness(stdio_server(env=pid_file_env(pid_file)))
        await asyncio.wait_for(harness.prompt("hello"), WAIT)
        first = await read_pid(pid_file)

        text = await harness.dispatch_command("mcp", "reconnect mock")

        assert (text or "").startswith("- mock: stdio (")
        assert (text or "").endswith(f"ready, {len(MOCK_TOOLS)} tools")
        await wait_gone(first)
        assert await read_pid(pid_file) != first

    async def test_reconnect_of_an_unknown_name_lists_the_known_ones(self, rig: Rig):
        harness = await rig.harness(stdio_server("one"), stdio_server("two"))

        text = await harness.dispatch_command("mcp", "reconnect nope")

        assert text == 'No MCP server is named "nope". The servers are: one, two.'

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            ("reconnect", "Use /mcp reconnect <name>."),
            (
                "restart one",
                "Use /mcp to see the servers, or /mcp reconnect <name> to connect one again.",
            ),
        ],
    )
    async def test_a_command_it_does_not_know_gets_a_hint(self, rig: Rig, args: str, expected: str):
        harness = await rig.harness(stdio_server("one"))

        assert await harness.dispatch_command("mcp", args) == expected


# ---------------------------------------------------------------------------
# The end
# ---------------------------------------------------------------------------


class TestEnd:
    async def test_closing_the_session_ends_the_server_processes(self, rig: Rig, tmp_path: Path):
        files = [tmp_path / "one.pid", tmp_path / "two.pid"]
        harness = await rig.harness(
            stdio_server("one", env=pid_file_env(files[0])),
            stdio_server("two", env=pid_file_env(files[1])),
        )
        await asyncio.wait_for(harness.prompt("hello"), WAIT)
        pids = [await read_pid(path) for path in files]

        await harness.close()

        for pid in pids:
            await wait_gone(pid)

    async def test_closing_during_a_start_ends_the_server_too(self, rig: Rig, tmp_path: Path):
        pid_file = tmp_path / "late.pid"
        harness = await rig.harness(
            stdio_server(env=pid_file_env(pid_file, ("PI_MCP_TEST_START_DELAY", "120")))
        )
        pid = await read_pid(pid_file)

        await harness.close()

        await wait_gone(pid)

    async def test_a_tool_call_in_flight_ends_when_the_session_closes(self, rig: Rig):
        llm = ToolScript(("mcp__mock__slow", {"seconds": 60}))
        harness = await rig.harness(stdio_server(), llm=llm)
        turn = asyncio.ensure_future(harness.prompt("a long call"))
        await until(lambda: len(llm.offered) >= 1)
        await asyncio.sleep(1.0)  # the call is on its way

        started = time.monotonic()
        await harness.close()

        await asyncio.wait_for(asyncio.gather(turn, return_exceptions=True), 20)
        assert time.monotonic() - started < 20
