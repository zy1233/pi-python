"""How the calls of one assistant message run, and how a caller reads the tool objects.

A caller that judges a call by the files (the CLI's Auto mode) needs two things from the
harness: ``tool_execution = "sequential"`` so a call is judged after the call before it has run,
and ``get_tool`` / ``get_tools`` so it can tell a built-in tool object from one that an extension
registered under the same name.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult, DoneEvent, Model, StartEvent, StreamOptions
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session


class _Params(BaseModel):
    message: str = ""


def _model() -> Model:
    return Model(provider="mock", model_id="m1")


async def _session() -> Session:
    return Session(await MemorySessionStorage.create(session_id="tool-execution"))


def _tool(name: str, log: list[str] | None = None) -> SimpleTool:
    async def run(_id, params, signal, on_update):
        if log is not None:
            log.append(f"run:{params.message}")
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    return SimpleTool(name, "", name, _Params, run)


async def _two_calls_stream(
    model: Model, context, options: StreamOptions | None = None
) -> AssistantMessageEventStream:
    last_user = max(
        (i for i, m in enumerate(context.messages) if getattr(m, "role", None) == "user"),
        default=-1,
    )
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages[last_user + 1 :]):
        return await mock_text_stream(model, context, options)
    stream = AssistantMessageEventStream()
    partial = _base_partial(
        model,
        [
            {"type": "toolCall", "id": "call_1", "name": "echo", "arguments": {"message": "one"}},
            {"type": "toolCall", "id": "call_2", "name": "echo", "arguments": {"message": "two"}},
        ],
    )
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def _run_two_calls(execution: str | None) -> list[str]:
    log: list[str] = []
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=_two_calls_stream,
        tools=[_tool("echo", log)],
    )
    if execution is not None:
        harness.tool_execution = execution  # type: ignore[assignment]

    def tool_call(event):
        log.append(f"decide:{event.input['message']}")
        return None

    harness.on("tool_call", tool_call)
    await harness.prompt("go")
    return log


async def test_the_harness_runs_the_calls_of_one_message_in_parallel_by_default():
    harness = AgentHarness(session=await _session(), model=_model(), stream_fn=mock_text_stream)
    assert harness.tool_execution == "parallel"


async def test_parallel_decides_every_call_before_any_call_runs():
    log = await _run_two_calls(None)
    assert log == ["decide:one", "decide:two", "run:one", "run:two"]


async def test_sequential_decides_a_call_after_the_call_before_it_has_run():
    log = await _run_two_calls("sequential")
    assert log == ["decide:one", "run:one", "decide:two", "run:two"]


async def test_the_mode_is_read_when_a_run_starts_not_when_the_harness_is_built():
    log: list[str] = []
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=_two_calls_stream,
        tools=[_tool("echo", log)],
    )
    harness.on("tool_call", lambda event: log.append(f"decide:{event.input['message']}"))

    await harness.prompt("first run")
    assert log == ["decide:one", "decide:two", "run:one", "run:two"]

    log.clear()
    harness.tool_execution = "sequential"
    await harness.prompt("second run")
    assert log == ["decide:one", "run:one", "decide:two", "run:two"]


async def test_get_tool_returns_the_object_that_was_registered_not_a_copy():
    echo = _tool("echo")
    harness = AgentHarness(
        session=await _session(), model=_model(), stream_fn=mock_text_stream, tools=[echo]
    )
    assert harness.get_tool("echo") is echo


async def test_get_tool_returns_none_for_a_name_that_is_not_registered():
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=mock_text_stream,
        tools=[_tool("echo")],
    )
    assert harness.get_tool("nope") is None


async def test_get_tools_lists_the_objects_active_or_not():
    first, second = _tool("first"), _tool("second")
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=mock_text_stream,
        tools=[first, second],
        active_tool_names=["first"],
    )
    tools = harness.get_tools()
    assert len(tools) == 2
    assert tools[0] is first
    assert tools[1] is second


async def test_replacing_a_tool_makes_get_tool_return_the_new_object():
    original, replacement = _tool("echo"), _tool("echo")
    harness = AgentHarness(
        session=await _session(), model=_model(), stream_fn=mock_text_stream, tools=[original]
    )
    await harness.set_tools([replacement])
    assert harness.get_tool("echo") is replacement
    assert harness.get_tool("echo") is not original


async def test_the_list_from_get_tools_is_a_copy_that_does_not_change_the_harness():
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=mock_text_stream,
        tools=[_tool("echo")],
    )
    harness.get_tools().clear()
    assert harness.get_tool("echo") is not None


@pytest.mark.parametrize("name", ["bash", "write", "edit"])
async def test_a_tool_with_the_name_of_a_built_in_one_is_a_different_object(name):
    builtin, impostor = _tool(name), _tool(name)
    harness = AgentHarness(
        session=await _session(), model=_model(), stream_fn=mock_text_stream, tools=[impostor]
    )
    assert harness.get_tool(name) is not builtin
