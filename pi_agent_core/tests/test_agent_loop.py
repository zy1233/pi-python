"""Agent loop tests with mock stream (mirrors pi agent-loop.test.ts scenarios)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from pydantic import BaseModel, Field

from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.agent_loop import run_agent_loop, run_agent_loop_continue
from pi_agent_core.messages import UserMessage
from pi_agent_core.tests.mock_stream import mock_error_stream, mock_text_stream, mock_tool_stream
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import (
    AgentContext,
    AgentLoopConfig,
    AgentToolResult,
    Model,
)


def _model() -> Model:
    return Model(provider="mock", model_id="mock-1")


async def _collect(
    prompts: list,
    context: AgentContext,
    config: AgentLoopConfig,
    stream_fn: Any,
) -> list:
    events: list = []

    async def emit(e: Any) -> None:
        events.append(e)

    await run_agent_loop(prompts, context, config, emit, stream_fn=stream_fn)
    return events


def _event_types(events: list) -> list[str]:
    return [e.type for e in events]


@pytest.mark.asyncio
async def test_simple_prompt_event_sequence():
    prompt = UserMessage(content="Hi", timestamp=int(time.time() * 1000))
    ctx = AgentContext(system_prompt="You are helpful.", messages=[], tools=[])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    events = await _collect([prompt], ctx, config, mock_text_stream)
    types = _event_types(events)

    assert types[0] == "agent_start"
    assert "turn_start" in types
    assert types.count("message_start") >= 2
    assert types.count("message_end") >= 2
    assert "message_update" in types
    assert types[-1] == "agent_end"


@pytest.mark.asyncio
async def test_tool_execution():
    class EchoParams(BaseModel):
        message: str = Field(description="text to echo")

    async def echo_execute(_id: str, params: EchoParams, signal, on_update) -> AgentToolResult:
        return AgentToolResult(
            content=[{"type": "text", "text": params.message}],
            details={},
        )

    tool = SimpleTool(
        name="echo",
        description="Echo",
        label="Echo",
        parameters=EchoParams,
        execute_fn=echo_execute,
    )

    prompt = UserMessage(content="echo", timestamp=int(time.time() * 1000))
    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    events = await _collect([prompt], ctx, config, mock_tool_stream)
    types = _event_types(events)

    assert "tool_execution_start" in types
    assert "tool_execution_end" in types
    tool_ends = [e for e in events if e.type == "tool_execution_end"]
    assert tool_ends[0].is_error is False


@pytest.mark.asyncio
async def test_tool_error_on_throw():
    class P(BaseModel):
        x: str

    async def fail(_id, params, signal, on_update):
        raise ValueError("tool failed")

    tool = SimpleTool(name="fail", description="", label="F", parameters=P, execute_fn=fail)
    prompt = UserMessage(content="go", timestamp=int(time.time() * 1000))
    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    events = await _collect([prompt], ctx, config, mock_tool_stream)
    tool_ends = [e for e in events if e.type == "tool_execution_end"]
    assert tool_ends[0].is_error is True


@pytest.mark.asyncio
async def test_should_stop_after_turn():
    prompt = UserMessage(content="Hi", timestamp=int(time.time() * 1000))
    ctx = AgentContext(system_prompt="", messages=[], tools=[])
    stopped = {"value": False}

    async def should_stop(ctx) -> bool:
        stopped["value"] = True
        return True

    config = AgentLoopConfig(
        model=_model(),
        convert_to_llm=default_convert_to_llm,
        should_stop_after_turn=should_stop,
    )

    events = await _collect([prompt], ctx, config, mock_text_stream)
    assert stopped["value"]
    assert _event_types(events)[-1] == "agent_end"
    assert _event_types(events).count("turn_start") == 1


@pytest.mark.asyncio
async def test_steering_messages():
    prompt = UserMessage(content="Hi", timestamp=int(time.time() * 1000))
    steering = UserMessage(content="Steer", timestamp=int(time.time() * 1000))
    polled = {"count": 0}

    async def get_steering():
        polled["count"] += 1
        if polled["count"] == 1:
            return [steering]
        return []

    ctx = AgentContext(system_prompt="", messages=[], tools=[])
    config = AgentLoopConfig(
        model=_model(),
        convert_to_llm=default_convert_to_llm,
        get_steering_messages=get_steering,
    )

    events = await _collect([prompt], ctx, config, mock_text_stream)
    user_ends = [
        e.message.content
        for e in events
        if e.type == "message_end" and getattr(e.message, "role", None) == "user"
    ]
    assert any("Steer" in str(c) for c in user_ends)


@pytest.mark.asyncio
async def test_error_stop_reason():
    prompt = UserMessage(content="Hi", timestamp=int(time.time() * 1000))
    ctx = AgentContext(system_prompt="", messages=[], tools=[])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    events = await _collect([prompt], ctx, config, mock_error_stream)
    turn_ends = [e for e in events if e.type == "turn_end"]
    assert turn_ends[-1].message.stopReason == "error"


@pytest.mark.asyncio
async def test_continue_from_tool_result():
    from pi_agent_core.messages import AssistantMessage, ToolResultMessage

    assistant = AssistantMessage(
        content=[{"type": "text", "text": "done"}],
        provider="mock",
        model="m",
        timestamp=int(time.time() * 1000),
    )
    tool_result = ToolResultMessage(
        toolCallId="c1",
        toolName="echo",
        content=[{"type": "text", "text": "ok"}],
        timestamp=int(time.time() * 1000),
    )
    ctx = AgentContext(system_prompt="", messages=[assistant, tool_result], tools=[])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    events: list = []

    async def emit(e):
        events.append(e)

    await run_agent_loop_continue(ctx, config, emit, stream_fn=mock_text_stream)
    assert _event_types(events)[0] == "agent_start"
    assert _event_types(events)[-1] == "agent_end"
