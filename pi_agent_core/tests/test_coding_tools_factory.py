"""Factory assembly tests + agent-loop integration for the built-in tools."""

from __future__ import annotations

import time

import pytest

from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.agent_loop import run_agent_loop
from pi_agent_core.coding_tools import (
    ALL_TOOL_NAMES,
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
    create_tool,
)
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import UserMessage
from pi_agent_core.tests.mock_stream import _base_partial
from pi_agent_core.types import (
    AgentContext,
    AgentLoopConfig,
    DoneEvent,
    Model,
    StartEvent,
)

# --- factories ---


def test_all_tool_names_matches_spec():
    assert {"read", "bash", "edit", "write", "grep", "find", "ls"} == ALL_TOOL_NAMES


def test_create_tool_dispatches_all_names(tmp_path):
    for name in sorted(ALL_TOOL_NAMES):
        tool = create_tool(name, str(tmp_path))
        assert tool.name == name


def test_create_tool_forwards_options(tmp_path):
    tool = create_tool("grep", str(tmp_path), use_fallback=True)
    assert tool.name == "grep"


def test_create_tool_unknown_name_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown tool name: 'cat'"):
        create_tool("cat", str(tmp_path))  # type: ignore[arg-type]


def test_coding_group_composition(tmp_path):
    tools = create_coding_tools(str(tmp_path))
    assert [t.name for t in tools] == ["read", "bash", "edit", "write"]


def test_read_only_group_composition(tmp_path):
    tools = create_read_only_tools(str(tmp_path))
    assert [t.name for t in tools] == ["read", "grep", "find", "ls"]


def test_create_all_tools_covers_every_name(tmp_path):
    tools = create_all_tools(str(tmp_path))
    assert set(tools) == ALL_TOOL_NAMES
    assert all(tools[name].name == name for name in tools)


# --- agent-loop integration (event-contract invariants, AGENTS.md #1/#2) ---


async def _read_tool_stream(model, context, options=None):
    """Mock LLM: first turn calls the read tool, second turn answers with text."""
    stream = AssistantMessageEventStream()
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        partial = _base_partial(model, [{"type": "text", "text": "done"}])
        reason = "stop"
    else:
        partial = _base_partial(
            model,
            [
                {
                    "type": "toolCall",
                    "id": "call_read",
                    "name": "read",
                    "arguments": {"path": "notes.txt"},
                }
            ],
        )
        partial.stopReason = "toolUse"
        reason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason=reason))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def test_read_only_tools_run_a_full_tool_loop(tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"hello from file")
    ctx = AgentContext(system_prompt="", messages=[], tools=create_read_only_tools(str(tmp_path)))
    config = AgentLoopConfig(
        model=Model(provider="mock", model_id="mock-1"),
        convert_to_llm=default_convert_to_llm,
    )
    prompt = UserMessage(content="read notes", timestamp=int(time.time() * 1000))

    events: list = []

    async def emit(e) -> None:
        events.append(e)

    await run_agent_loop([prompt], ctx, config, emit, stream_fn=_read_tool_stream)
    types = [e.type for e in events]

    # Event contract: tool turn + follow-up text turn, closed by agent_end.
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert types.count("turn_start") == 2
    assert types.count("turn_end") == 2

    tool_end = next(e for e in events if e.type == "tool_execution_end")
    assert tool_end.is_error is False
    assert tool_end.tool_name == "read"
    assert tool_end.result.content == [{"type": "text", "text": "hello from file"}]

    # The toolResult message persists between assistant end and next turn.
    tool_result_starts = [
        e
        for e in events
        if e.type == "message_start" and getattr(e.message, "role", None) == "toolResult"
    ]
    assert len(tool_result_starts) == 1
    assert tool_result_starts[0].message.isError is False


async def test_read_only_tools_surface_errors_as_error_results(tmp_path):
    ctx = AgentContext(system_prompt="", messages=[], tools=create_read_only_tools(str(tmp_path)))
    config = AgentLoopConfig(
        model=Model(provider="mock", model_id="mock-1"),
        convert_to_llm=default_convert_to_llm,
    )
    prompt = UserMessage(content="read missing", timestamp=int(time.time() * 1000))

    events: list = []

    async def emit(e) -> None:
        events.append(e)

    # notes.txt does not exist: the tool raises, the loop converts to an
    # is_error tool result, and the run still completes normally.
    await run_agent_loop([prompt], ctx, config, emit, stream_fn=_read_tool_stream)

    tool_end = next(e for e in events if e.type == "tool_execution_end")
    assert tool_end.is_error is True
    assert [e.type for e in events][-1] == "agent_end"
