"""What an extension reads back from its session, what its tools replace, and how they are called
(audit F7-02, F7-06, F7-08)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from pi_agent_core.extensions import ExtensionAPI, ToolDefinition
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult, Model
from pi_agent_harness import AgentHarness, CustomEntry, MemorySessionStorage, Session

HARNESS_LOGGER = "pi_agent_harness.agent_harness"


class _NoParams(BaseModel):
    pass


async def _run(tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None):
    return AgentToolResult(content=[{"type": "text", "text": "ok"}])


def _definition(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description="d", parameters=_NoParams, execute=_run)


def _built_in(name: str) -> SimpleTool:
    return SimpleTool(name, "a built-in tool", name.title(), _NoParams, _run)


def _capture(into: list[ExtensionAPI]) -> Callable[[ExtensionAPI], None]:
    def activate(pi: ExtensionAPI) -> None:
        into.append(pi)

    return activate


async def _harness(
    *extensions: Callable[[ExtensionAPI], None],
    tools: list[Any] | None = None,
    entries: tuple[tuple[str, Any], ...] = (),
) -> tuple[AgentHarness, Session]:
    """A loaded harness. *entries* are in the session before it starts, as after a restart."""
    session = Session(await MemorySessionStorage.create(session_id="f7"))
    for custom_type, data in entries:
        await session.append_custom_entry(custom_type, data)
    harness = AgentHarness(
        session=session,
        model=Model(provider="mock", model_id="m1"),
        stream_fn=mock_text_stream,
        tools=tools,
        extensions=list(extensions),
    )
    await harness.load_extensions()
    return harness, session


def _data(pi: ExtensionAPI, custom_type: str) -> list[Any]:
    return [entry.data for entry in pi.get_custom_entries(custom_type)]


# ---------------------------------------------------------------------------
# F7-02: an extension reads back what it wrote
# ---------------------------------------------------------------------------


class TestCustomEntriesSeeOwnWrites:
    """``get_custom_entries`` is documented as "all entries of the session". It was the
    entries of the session as it was when the extensions loaded, so an extension that wrote an
    entry and read it back in the same session did not find it."""

    async def test_an_entry_written_in_this_session_is_read_back(self) -> None:
        pis: list[ExtensionAPI] = []
        await _harness(_capture(pis))
        (pi,) = pis

        pi.append_entry("note", {"n": 1})

        (entry,) = pi.get_custom_entries("note")
        assert isinstance(entry, CustomEntry)
        assert (entry.type, entry.customType, entry.data) == ("custom", "note", {"n": 1})

    async def test_the_entries_are_in_session_order_and_of_the_asked_type_only(self) -> None:
        pis: list[ExtensionAPI] = []
        await _harness(
            _capture(pis), entries=(("note", {"n": 0}), ("other", {"x": 1}), ("note", {"n": 1}))
        )
        (pi,) = pis

        pi.append_entry("note", {"n": 2})
        pi.append_entry("other", {"x": 2})
        pi.append_entry("note", {"n": 3})

        assert _data(pi, "note") == [{"n": 0}, {"n": 1}, {"n": 2}, {"n": 3}]
        assert _data(pi, "other") == [{"x": 1}, {"x": 2}]
        assert _data(pi, "none") == []

    async def test_an_entry_is_read_once_after_the_session_has_written_it(self) -> None:
        pis: list[ExtensionAPI] = []
        harness, session = await _harness(_capture(pis), entries=(("note", {"n": 0}),))
        (pi,) = pis
        pi.append_entry("note", {"n": 1})

        await harness._flush_pending_session_writes()

        assert _data(pi, "note") == [{"n": 0}, {"n": 1}]  # not three
        written = [e for e in await session.get_entries() if e.type == "custom"]
        assert [e.data for e in written] == [{"n": 0}, {"n": 1}]

    async def test_an_entry_that_the_session_has_not_written_yet_has_no_id_but_a_time(self) -> None:
        pis: list[ExtensionAPI] = []
        await _harness(_capture(pis), entries=(("note", {"n": 0}),))
        (pi,) = pis

        pi.append_entry("note", {"n": 1})

        written, queued = pi.get_custom_entries("note")
        assert written.id != ""  # the session gave this one its id
        assert queued.id == ""  # and has not given one to this one yet
        assert queued.timestamp  # an ISO time, like every entry

    async def test_an_entry_written_while_the_extension_activates_is_there_at_session_start(
        self,
    ) -> None:
        seen: list[list[Any]] = []

        def activate(pi: ExtensionAPI) -> None:
            pi.append_entry("note", {"n": 1})
            pi.on("session_start", lambda event: seen.append(_data(pi, "note")))

        await _harness(activate, entries=(("note", {"n": 0}),))

        assert seen == [[{"n": 0}, {"n": 1}]]

    async def test_what_was_persisted_before_is_still_read(self) -> None:
        """What goal-x does: it reads its state at ``session_start`` and writes nothing first."""
        seen: list[list[Any]] = []

        def activate(pi: ExtensionAPI) -> None:
            pi.on("session_start", lambda event: seen.append(_data(pi, "note")))

        await _harness(activate, entries=(("note", {"n": 0}),))

        assert seen == [[{"n": 0}]]


# ---------------------------------------------------------------------------
# F7-06: an extension tool that takes the name of a built-in tool is logged
# ---------------------------------------------------------------------------


def _replacement_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == HARNESS_LOGGER
        and r.levelno == logging.WARNING
        and "built-in" in r.getMessage()
    ]


class TestToolsThatTakeABuiltInName:
    """The harness looks up the permissions of a call by the name, so a tool that takes the
    name of ``bash`` or ``write`` is judged by what it declares about itself. For an extension
    the user trusts that is how it is meant to be. It should not happen without a trace."""

    def _extension(self, name: str, tool: str) -> Callable[[ExtensionAPI], None]:
        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(_definition(tool))

        activate.__qualname__ = name  # the name of the extension, which the log line shows
        return activate

    async def test_the_log_names_the_tool_and_the_extension(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        built_in = _built_in("read")

        with caplog.at_level(logging.WARNING, logger=HARNESS_LOGGER):
            harness, _ = await _harness(self._extension("reader-ext", "read"), tools=[built_in])

        (message,) = _replacement_warnings(caplog)
        assert "'read'" in message
        assert "reader-ext" in message
        assert harness._tools["read"] is not built_in  # it is still replaced: that is allowed

    async def test_a_new_name_is_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=HARNESS_LOGGER):
            await _harness(self._extension("fresh-ext", "fresh"), tools=[_built_in("read")])

        assert _replacement_warnings(caplog) == []

    async def test_loading_another_extension_does_not_log_the_first_one_again(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Every extension that loads after the session started applies all registrations
        again, so the first one's tool is met a second time."""
        harness, _ = await _harness(
            self._extension("reader-ext", "read"), tools=[_built_in("read")]
        )
        caplog.clear()

        with caplog.at_level(logging.WARNING, logger=HARNESS_LOGGER):
            harness.load_extension(self._extension("second-ext", "second"), name="second-ext")

        assert _replacement_warnings(caplog) == []
        assert "second" in harness._tools

    async def test_a_tool_registered_after_loading_is_logged_too(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pis: list[ExtensionAPI] = []
        harness, _ = await _harness(_capture(pis), tools=[_built_in("write")])
        (pi,) = pis

        with caplog.at_level(logging.WARNING, logger=HARNESS_LOGGER):
            pi.register_tool(_definition("write"))

        (message,) = _replacement_warnings(caplog)
        assert "'write'" in message
        assert harness._tools["write"] is not None

    async def test_a_tool_registered_twice_by_extensions_is_not_called_a_built_in(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Extension against extension is the registry's business (it logs that itself)."""
        pis: list[ExtensionAPI] = []
        await _harness(_capture(pis))
        (pi,) = pis
        pi.register_tool(_definition("shared"))

        with caplog.at_level(logging.WARNING, logger=HARNESS_LOGGER):
            pi.register_tool(_definition("shared"))

        assert _replacement_warnings(caplog) == []


# ---------------------------------------------------------------------------
# F7-08: the call that the ``execute`` of an extension tool gets
# ---------------------------------------------------------------------------


class TestToolExecuteCall:
    """``ExecuteFn`` and the spec (§3.4) say: four positional arguments. The code keeps to it."""

    @staticmethod
    def _definition_with(execute: Callable[..., Any]) -> Callable[[ExtensionAPI], None]:
        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(name="probe", description="d", parameters=_NoParams, execute=execute)
            )

        return activate

    async def test_execute_gets_the_id_the_params_the_signal_and_the_callback(self) -> None:
        seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def execute(*args: Any, **kwargs: Any) -> AgentToolResult:
            seen.append((args, kwargs))
            return AgentToolResult(content=[{"type": "text", "text": "ok"}])

        harness, _ = await _harness(self._definition_with(execute))
        params, signal, on_update = _NoParams(), object(), object()

        await harness._tools["probe"].execute("call-1", params, signal, on_update)

        ((args, kwargs),) = seen
        assert kwargs == {}
        assert len(args) == 4
        assert args[0] == "call-1"
        assert args[1] is params
        assert args[2] is signal
        assert args[3] is on_update

    async def test_a_sync_function_is_fine_too(self) -> None:
        def execute(tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None):
            return AgentToolResult(content=[{"type": "text", "text": tool_call_id}])

        harness, _ = await _harness(self._definition_with(execute))

        result = await harness._tools["probe"].execute("call-1", _NoParams(), None, None)

        assert result.content[0]["text"] == "call-1"

    async def test_a_function_of_two_arguments_fails_when_called_not_when_registered(self) -> None:
        def two_arguments(tool_call_id: str, params: Any):  # the habit of upstream TS tools
            return AgentToolResult(content=[{"type": "text", "text": "ok"}])

        harness, _ = await _harness(self._definition_with(two_arguments))  # no error here

        with pytest.raises(TypeError):
            await harness._tools["probe"].execute("call-1", _NoParams(), None, None)
