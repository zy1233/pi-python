"""The harness side of the MCP servers a client passes (design: docs/specs/2026-10-23-mcp-
extension-design.md).

The harness connects to nothing. It keeps the list the host gives it (``set_client_mcp_servers``),
remembers which servers an extension claimed, can show the user a line an extension sends
(``notify`` -> ``NoticeEvent``), and awaits the "turn barriers" of extensions before it builds the
tool list of a turn.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from pi_agent_core.extensions import ClientMcpServer, ExtensionAPI, ToolDefinition
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import AgentToolResult, Model
from pi_agent_harness import AgentHarness, AgentHarnessError, MemorySessionStorage, Session
from pi_agent_harness.types import NoticeEvent

WAIT = 5  # seconds: a broken test fails instead of hanging


def server(name: str, transport: str = "stdio") -> ClientMcpServer:
    return ClientMcpServer(name=name, transport=transport)  # type: ignore[arg-type]


class Recorder:
    """A ``stream_fn`` that records the tools each call was offered."""

    def __init__(self) -> None:
        self.tool_names: list[list[str]] = []

    async def __call__(self, model: Model, context: Any, options: Any = None) -> Any:
        self.tool_names.append([getattr(tool, "name", "?") for tool in context.tools or []])
        return await mock_text_stream(model, context, options)


async def make_harness(
    *extensions: Callable[[ExtensionAPI], None], llm: Any = None
) -> AgentHarness:
    return AgentHarness(
        session=Session(await MemorySessionStorage.create(session_id="client-mcp")),
        model=Model(provider="mock", model_id="m1"),
        stream_fn=llm or mock_text_stream,
        extensions=list(extensions),
    )


async def until(condition: Callable[[], Any], *, timeout: float = WAIT) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


# ---------------------------------------------------------------------------
# What the client passed, and what an extension took
# ---------------------------------------------------------------------------


class TestClientServers:
    async def test_the_servers_are_what_the_host_set_and_none_before(self):
        harness = await make_harness()
        assert harness.client_mcp_servers == ()
        assert harness.unclaimed_client_mcp_servers == ()

        harness.set_client_mcp_servers([server("a"), server("b", "http")])

        assert [s.name for s in harness.client_mcp_servers] == ["a", "b"]
        assert isinstance(harness.client_mcp_servers, tuple)

    async def test_setting_them_again_replaces_the_list(self):
        harness = await make_harness()
        harness.set_client_mcp_servers([server("a")])
        harness.set_client_mcp_servers([server("b")])

        assert [s.name for s in harness.client_mcp_servers] == ["b"]

    async def test_every_server_is_unclaimed_until_an_extension_claims_it(self):
        harness = await make_harness()
        harness.set_client_mcp_servers([server("a"), server("b")])
        await harness.load_extensions()

        assert [s.name for s in harness.unclaimed_client_mcp_servers] == ["a", "b"]

    async def test_a_claimed_server_is_no_longer_unclaimed(self):
        def extension(pi: ExtensionAPI) -> None:
            pi.claim_client_mcp_servers(["b"])

        harness = await make_harness(extension)
        harness.set_client_mcp_servers([server("a"), server("b"), server("c")])
        await harness.load_extensions()

        assert [s.name for s in harness.unclaimed_client_mcp_servers] == ["a", "c"]
        assert [s.name for s in harness.client_mcp_servers] == ["a", "b", "c"]  # still passed

    async def test_an_extension_sees_the_servers_while_it_activates(self):
        seen: list[str] = []

        def extension(pi: ExtensionAPI) -> None:
            seen.extend(s.name for s in pi.client_mcp_servers)
            pi.claim_client_mcp_servers(seen)

        harness = await make_harness(extension)
        harness.set_client_mcp_servers([server("a"), server("b")])
        await harness.load_extensions()

        assert seen == ["a", "b"]
        assert harness.unclaimed_client_mcp_servers == ()

    async def test_claiming_a_name_nobody_passed_changes_nothing(self):
        def extension(pi: ExtensionAPI) -> None:
            pi.claim_client_mcp_servers(["nobody", 3, None])  # type: ignore[list-item]

        harness = await make_harness(extension)
        harness.set_client_mcp_servers([server("a")])
        await harness.load_extensions()

        assert [s.name for s in harness.unclaimed_client_mcp_servers] == ["a"]

    async def test_two_extensions_may_claim_different_servers(self):
        def first(pi: ExtensionAPI) -> None:
            pi.claim_client_mcp_servers(["a"])

        def second(pi: ExtensionAPI) -> None:
            pi.claim_client_mcp_servers(["b"])

        harness = await make_harness(first, second)
        harness.set_client_mcp_servers([server("a"), server("b"), server("c")])
        await harness.load_extensions()

        assert [s.name for s in harness.unclaimed_client_mcp_servers] == ["c"]

    async def test_the_list_cannot_change_once_the_extensions_have_loaded(self):
        harness = await make_harness()
        await harness.load_extensions()

        with pytest.raises(AgentHarnessError, match="can no longer change") as raised:
            harness.set_client_mcp_servers([server("a")])
        assert raised.value.code == "invalid_state"
        assert harness.client_mcp_servers == ()

    async def test_the_first_prompt_loads_the_extensions_too(self):
        harness = await make_harness()
        await harness.prompt("hello")  # nothing called load_extensions() first

        with pytest.raises(AgentHarnessError, match="can no longer change"):
            harness.set_client_mcp_servers([server("a")])


# ---------------------------------------------------------------------------
# A line for the user
# ---------------------------------------------------------------------------


async def loaded_with_events() -> tuple[AgentHarness, list[Any], Any]:
    harness = await make_harness()
    await harness.load_extensions()
    events: list[Any] = []

    async def on_event(event: Any, signal: Any = None) -> None:
        events.append(event)

    harness.subscribe(on_event)
    return harness, events, harness._extension_bridge


def notices(events: list[Any]) -> list[str]:
    return [event.text for event in events if getattr(event, "type", None) == "notice"]


class TestNotify:
    async def test_a_line_from_an_extension_reaches_the_subscribers(self):
        _, events, bridge = await loaded_with_events()

        bridge.notify("MCP server files failed to start")
        await until(lambda: notices(events))

        assert notices(events) == ["MCP server files failed to start"]
        assert isinstance(events[0], NoticeEvent)

    async def test_the_lines_keep_their_order(self):
        _, events, bridge = await loaded_with_events()

        for text in ("one", "two", "three"):
            bridge.notify(text)
        await until(lambda: len(notices(events)) == 3)

        assert notices(events) == ["one", "two", "three"]

    async def test_it_is_not_part_of_the_conversation(self):
        harness, events, bridge = await loaded_with_events()

        bridge.notify("for the user, not for the model")
        await until(lambda: notices(events))

        context = await harness.session.build_context()
        assert context.messages == []

    @pytest.mark.parametrize("text", ["", "   ", "\n\t", None, 3])
    async def test_a_line_without_text_is_dropped(self, text: Any):
        _, events, bridge = await loaded_with_events()

        bridge.notify(text)
        await asyncio.sleep(0.05)

        assert notices(events) == []

    async def test_a_subscriber_that_fails_does_not_lose_the_line_for_the_others(self):
        harness, events, bridge = await loaded_with_events()

        async def broken(event: Any, signal: Any = None) -> None:
            raise RuntimeError("subscriber bug")

        harness.subscribe(broken)
        bridge.notify("still arrives")
        await until(lambda: notices(events))

        assert notices(events) == ["still arrives"]

    async def test_nothing_is_sent_once_the_session_is_closed(self):
        harness, events, bridge = await loaded_with_events()
        await harness.close()

        bridge.notify("too late")
        await asyncio.sleep(0.05)

        assert notices(events) == []

    def test_without_a_running_loop_the_line_is_dropped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        async def build() -> AgentHarness:
            return await make_harness()

        harness = asyncio.run(build())  # the loop is gone when ``notify`` is called

        with caplog.at_level(logging.WARNING, logger="pi_agent_harness.agent_harness"):
            harness._notify("nobody can take this")

        assert "no running event loop" in caplog.text

    def test_a_line_sent_from_another_thread_is_dropped_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ):
        async def build() -> AgentHarness:
            return await make_harness()

        harness = asyncio.run(build())
        errors: list[BaseException] = []

        def call() -> None:
            try:
                harness._notify("from a thread")
            except BaseException as exc:  # the point is that nothing escapes
                errors.append(exc)

        thread = threading.Thread(target=call)
        thread.start()
        thread.join(WAIT)

        assert errors == []


# ---------------------------------------------------------------------------
# Turn barriers
# ---------------------------------------------------------------------------


class SupplyParams(BaseModel):
    pass


async def supply_execute(
    tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None
) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": "supplied"}])


SUPPLIED_TOOL = ToolDefinition(
    name="supplied",
    description="A tool that an extension adds in the background",
    parameters=SupplyParams,
    execute=supply_execute,
    label="Supplied",
)


class TestTurnBarriers:
    async def test_with_no_barrier_waiting_returns_at_once(self):
        harness = await make_harness()

        await asyncio.wait_for(harness.wait_turn_barriers(), 1)

    async def test_the_turn_waits_for_a_barrier_before_it_builds_the_tool_list(self):
        # A barrier that adds a tool: the tool must be in the list of the very first turn.
        llm = Recorder()

        def extension(pi: ExtensionAPI) -> None:
            async def settle() -> None:
                await asyncio.sleep(0.05)
                if "supplied" not in pi.get_active_tools():
                    pi.register_tool(SUPPLIED_TOOL)

            pi.add_turn_barrier(settle)

        harness = await make_harness(extension, llm=llm)

        await asyncio.wait_for(harness.prompt("hello"), WAIT)

        assert llm.tool_names == [["supplied"]]

    async def test_the_turn_does_not_start_while_a_barrier_is_open(self):
        llm = Recorder()
        release = asyncio.Event()

        def extension(pi: ExtensionAPI) -> None:
            pi.add_turn_barrier(release.wait)

        harness = await make_harness(extension, llm=llm)
        turn = asyncio.create_task(harness.prompt("hello"))
        await until(lambda: harness.phase == "turn")
        await asyncio.sleep(0.05)

        assert llm.tool_names == []  # the model has not been asked
        assert not turn.done()

        release.set()
        await asyncio.wait_for(turn, WAIT)
        assert len(llm.tool_names) == 1

    async def test_the_turn_waits_for_all_barriers(self):
        llm = Recorder()
        first, second = asyncio.Event(), asyncio.Event()

        def extension(pi: ExtensionAPI) -> None:
            pi.add_turn_barrier(first.wait)
            pi.add_turn_barrier(second.wait)

        harness = await make_harness(extension, llm=llm)
        turn = asyncio.create_task(harness.prompt("hello"))
        await until(lambda: harness.phase == "turn")

        first.set()
        await asyncio.sleep(0.05)
        assert llm.tool_names == []  # one is still open

        second.set()
        await asyncio.wait_for(turn, WAIT)
        assert len(llm.tool_names) == 1

    async def test_the_barriers_run_together_not_one_after_the_other(self):
        started: list[str] = []
        both = asyncio.Event()

        def extension(pi: ExtensionAPI) -> None:
            async def barrier(name: str) -> None:
                started.append(name)
                if len(started) == 2:
                    both.set()
                await both.wait()  # only ends if the other one has started as well

            pi.add_turn_barrier(lambda: barrier("a"))
            pi.add_turn_barrier(lambda: barrier("b"))

        harness = await make_harness(extension)

        await asyncio.wait_for(harness.prompt("hello"), WAIT)

        assert sorted(started) == ["a", "b"]

    async def test_a_barrier_may_be_a_plain_function(self):
        calls: list[str] = []

        def extension(pi: ExtensionAPI) -> None:
            pi.add_turn_barrier(lambda: calls.append("plain"))  # type: ignore[arg-type, return-value]

        harness = await make_harness(extension)
        await asyncio.wait_for(harness.prompt("hello"), WAIT)

        assert calls == ["plain"]

    async def test_a_barrier_that_raises_is_logged_and_the_turn_goes_on(
        self, caplog: pytest.LogCaptureFixture
    ):
        llm = Recorder()

        def extension(pi: ExtensionAPI) -> None:
            async def broken() -> None:
                raise RuntimeError("barrier bug")

            pi.add_turn_barrier(broken)

        harness = await make_harness(extension, llm=llm)

        with caplog.at_level(logging.WARNING, logger="pi_agent_harness.agent_harness"):
            result = await asyncio.wait_for(harness.prompt("hello"), WAIT)

        assert result.stopReason != "error"
        assert len(llm.tool_names) == 1
        assert "A turn barrier of an extension failed" in caplog.text

    async def test_one_barrier_that_raises_does_not_cut_the_others_short(self):
        llm = Recorder()
        release = asyncio.Event()

        def extension(pi: ExtensionAPI) -> None:
            async def broken() -> None:
                raise RuntimeError("barrier bug")

            pi.add_turn_barrier(broken)
            pi.add_turn_barrier(release.wait)

        harness = await make_harness(extension, llm=llm)
        turn = asyncio.create_task(harness.prompt("hello"))
        await until(lambda: harness.phase == "turn")
        await asyncio.sleep(0.05)

        assert llm.tool_names == []  # the healthy barrier is still open

        release.set()
        await asyncio.wait_for(turn, WAIT)
        assert len(llm.tool_names) == 1

    async def test_every_turn_calls_the_barrier_again(self):
        calls: list[int] = []

        def extension(pi: ExtensionAPI) -> None:
            pi.add_turn_barrier(lambda: calls.append(len(calls)))  # type: ignore[arg-type, return-value]

        harness = await make_harness(extension)
        await asyncio.wait_for(harness.prompt("one"), WAIT)
        await asyncio.wait_for(harness.prompt("two"), WAIT)

        assert calls == [0, 1]

    async def test_cancelling_the_wait_leaves_the_harness_idle(self):
        # ``prompt()`` can be cancelled while it waits for a barrier (the ACP agent does it for
        # ``session/cancel``). The phase must not stay "turn": nothing else would reset it.
        release = asyncio.Event()

        def extension(pi: ExtensionAPI) -> None:
            pi.add_turn_barrier(release.wait)

        harness = await make_harness(extension)
        turn = asyncio.create_task(harness.prompt("hello"))
        await until(lambda: harness.phase == "turn")

        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        assert harness.phase == "idle"
        await asyncio.wait_for(harness.wait_for_idle(), 1)

        release.set()  # and the harness is usable afterwards
        result = await asyncio.wait_for(harness.prompt("again"), WAIT)
        assert result.stopReason != "error"

    async def test_the_wait_can_be_raced_against_another_signal_and_then_repeated(self):
        # The ACP agent calls ``wait_turn_barriers()`` itself, so it can end the wait on
        # ``session/cancel``. Whatever finished stays finished: the call that follows does not
        # wait twice.
        release = asyncio.Event()
        calls: list[str] = []

        def extension(pi: ExtensionAPI) -> None:
            async def barrier() -> None:
                calls.append("called")
                await release.wait()

            pi.add_turn_barrier(barrier)

        harness = await make_harness(extension)
        await harness.load_extensions()  # the barrier exists once the extension has activated
        first = asyncio.create_task(harness.wait_turn_barriers())
        await until(lambda: calls)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        release.set()
        await asyncio.wait_for(harness.wait_turn_barriers(), 1)  # returns at once

        assert calls == ["called", "called"]
