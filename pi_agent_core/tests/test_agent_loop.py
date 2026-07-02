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
async def test_parallel_tools_run_concurrently():
    """D1: parallel mode must overlap executions; end events fire in completion order,
    tool result messages persist in source order."""
    import asyncio

    from pi_agent_core.event_stream import AssistantMessageEventStream
    from pi_agent_core.tests.mock_stream import _base_partial
    from pi_agent_core.types import DoneEvent, StartEvent

    async def slow(_id, params, signal, on_update) -> AgentToolResult:
        await asyncio.sleep(0.4)
        return AgentToolResult(content=[{"type": "text", "text": "slow"}], details={})

    async def fast(_id, params, signal, on_update) -> AgentToolResult:
        await asyncio.sleep(0.05)
        return AgentToolResult(content=[{"type": "text", "text": "fast"}], details={})

    tools = [
        SimpleTool(name="slow", description="", label="S", parameters={}, execute_fn=slow),
        SimpleTool(name="fast", description="", label="F", parameters={}, execute_fn=fast),
    ]

    async def two_tools_stream(model, context, options=None):
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)
        stream = AssistantMessageEventStream()
        partial = _base_partial(
            model,
            [
                {"type": "toolCall", "id": "c_slow", "name": "slow", "arguments": {}},
                {"type": "toolCall", "id": "c_fast", "name": "fast", "arguments": {}},
            ],
        )
        partial.stopReason = "toolUse"
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream

    prompt = UserMessage(content="go", timestamp=int(time.time() * 1000))
    ctx = AgentContext(system_prompt="", messages=[], tools=tools)
    config = AgentLoopConfig(
        model=_model(), convert_to_llm=default_convert_to_llm, tool_execution="parallel"
    )

    t0 = time.monotonic()
    events = await _collect([prompt], ctx, config, two_tools_stream)
    elapsed = time.monotonic() - t0

    end_order = [e.tool_call_id for e in events if e.type == "tool_execution_end"]
    result_order = [
        e.message.toolCallId
        for e in events
        if e.type == "message_end" and getattr(e.message, "role", None) == "toolResult"
    ]
    # Sequential execution would finish c_slow first and take ~0.45s.
    assert end_order == ["c_fast", "c_slow"]
    assert result_order == ["c_slow", "c_fast"]
    assert elapsed < 0.55


def _single_tool_stream(tool_name: str, arguments: dict | None = None):
    """One assistant message with a single tool call, then a final text response."""
    from pi_agent_core.event_stream import AssistantMessageEventStream
    from pi_agent_core.tests.mock_stream import _base_partial
    from pi_agent_core.types import DoneEvent, StartEvent

    async def stream_fn(model, context, options=None):
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)
        stream = AssistantMessageEventStream()
        partial = _base_partial(
            model,
            [{"type": "toolCall", "id": "c1", "name": tool_name, "arguments": arguments or {}}],
        )
        partial.stopReason = "toolUse"
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream

    return stream_fn


@pytest.mark.asyncio
async def test_tool_update_events_are_realtime():
    """B3: sync on_update calls must deliver update events while the tool still runs."""
    import asyncio
    import time as time_mod

    timeline: dict[str, float] = {}
    t0 = time_mod.monotonic()

    async def updater(_id, params, signal, on_update) -> AgentToolResult:
        on_update(AgentToolResult(content=[{"type": "text", "text": "50%"}], details={}))
        await asyncio.sleep(0.2)
        timeline["tool_finished"] = time_mod.monotonic() - t0
        return AgentToolResult(content=[{"type": "text", "text": "100%"}], details={})

    tool = SimpleTool(name="u", description="", label="U", parameters={}, execute_fn=updater)
    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    events: list = []

    async def emit(e):
        events.append(e)
        if e.type == "tool_execution_update":
            timeline["update_emitted"] = time_mod.monotonic() - t0

    await run_agent_loop(
        [UserMessage(content="go", timestamp=int(time.time() * 1000))],
        ctx,
        config,
        emit,
        stream_fn=_single_tool_stream("u"),
    )

    assert "update_emitted" in timeline, "sync on_update call must not be dropped"
    assert timeline["update_emitted"] < timeline["tool_finished"]


@pytest.mark.asyncio
async def test_tool_update_awaitable_backcompat():
    """B3: legacy tools that await on_update's return value keep working."""
    delivered = {"n": 0}

    async def updater(_id, params, signal, on_update) -> AgentToolResult:
        result = on_update(AgentToolResult(content=[{"type": "text", "text": "x"}], details={}))
        if result is not None:
            await result
        return AgentToolResult(content=[{"type": "text", "text": "done"}], details={})

    tool = SimpleTool(name="u", description="", label="U", parameters={}, execute_fn=updater)
    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm)

    async def emit(e):
        if e.type == "tool_execution_update":
            delivered["n"] += 1

    await run_agent_loop(
        [UserMessage(content="go", timestamp=int(time.time() * 1000))],
        ctx,
        config,
        emit,
        stream_fn=_single_tool_stream("u"),
    )
    assert delivered["n"] == 1


@pytest.mark.asyncio
async def test_after_tool_call_merges_content_and_details():
    """D4: content and details returned together must both apply."""
    from pi_agent_core.types import AfterToolCallResult

    async def t(_id, params, signal, on_update) -> AgentToolResult:
        return AgentToolResult(content=[{"type": "text", "text": "orig"}], details={"orig": True})

    async def after(ctx, signal):
        return AfterToolCallResult(
            content=[{"type": "text", "text": "replaced"}],
            details={"replaced": True},
        )

    tool = SimpleTool(name="t", description="", label="T", parameters={}, execute_fn=t)
    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(
        model=_model(), convert_to_llm=default_convert_to_llm, after_tool_call=after
    )

    results = []

    async def emit(e):
        if e.type == "tool_execution_end":
            results.append(e.result)

    await run_agent_loop(
        [UserMessage(content="go", timestamp=int(time.time() * 1000))],
        ctx,
        config,
        emit,
        stream_fn=_single_tool_stream("t"),
    )
    assert results[0].content == [{"type": "text", "text": "replaced"}]
    assert results[0].details == {"replaced": True}


@pytest.mark.asyncio
async def test_prepare_next_turn_applies_thinking_level_without_mutating_config():
    """D3: turn updates apply thinking_level and must not mutate the caller's config."""
    from pi_agent_core.types import AgentLoopTurnUpdate

    seen_reasoning: list = []

    async def capture_stream(model, context, options=None):
        seen_reasoning.append(options.reasoning if options else None)
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)
        return await _single_tool_stream("echo", {"message": "hi"})(model, context, options)

    class EchoParams(BaseModel):
        message: str

    async def echo(_id, params, signal, on_update) -> AgentToolResult:
        return AgentToolResult(content=[{"type": "text", "text": params.message}], details={})

    tool = SimpleTool(
        name="echo", description="", label="E", parameters=EchoParams, execute_fn=echo
    )

    async def prepare(next_ctx):
        return AgentLoopTurnUpdate(thinking_level="high")

    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(
        model=_model(),
        convert_to_llm=default_convert_to_llm,
        prepare_next_turn=prepare,
        thinking_level="off",
    )

    async def emit(e):
        pass

    await run_agent_loop(
        [UserMessage(content="go", timestamp=int(time.time() * 1000))],
        ctx,
        config,
        emit,
        stream_fn=capture_stream,
    )

    assert seen_reasoning[0] == "off"
    assert seen_reasoning[1] == "high"
    # caller-owned config must not be mutated
    assert config.thinking_level == "off"


def _endless_tool_stream():
    """Always answers with a tool call — drives an infinite loop unless guarded."""
    from pi_agent_core.event_stream import AssistantMessageEventStream
    from pi_agent_core.tests.mock_stream import _base_partial
    from pi_agent_core.types import DoneEvent, StartEvent

    counter = {"n": 0}

    async def stream_fn(model, context, options=None):
        counter["n"] += 1
        stream = AssistantMessageEventStream()
        partial = _base_partial(
            model,
            [
                {
                    "type": "toolCall",
                    "id": f"c{counter['n']}",
                    "name": "noop",
                    "arguments": {},
                }
            ],
        )
        partial.stopReason = "toolUse"
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream

    return stream_fn


def _noop_tool() -> SimpleTool:
    async def noop(_id, params, signal, on_update) -> AgentToolResult:
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    return SimpleTool(name="noop", description="", label="N", parameters={}, execute_fn=noop)


@pytest.mark.asyncio
async def test_max_turns_raises():
    """#2: a tool loop that never converges must stop at max_turns."""
    from pi_agent_core.types import MaxTurnsExceededError

    ctx = AgentContext(system_prompt="", messages=[], tools=[_noop_tool()])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm, max_turns=3)

    turn_starts = {"n": 0}

    async def emit(e):
        if e.type == "turn_start":
            turn_starts["n"] += 1

    with pytest.raises(MaxTurnsExceededError, match="max_turns=3"):
        await run_agent_loop(
            [UserMessage(content="go", timestamp=int(time.time() * 1000))],
            ctx,
            config,
            emit,
            stream_fn=_endless_tool_stream(),
        )
    assert turn_starts["n"] == 3


@pytest.mark.asyncio
async def test_max_turns_not_hit_on_normal_run():
    """#2: runs that finish within the budget are unaffected."""
    ctx = AgentContext(system_prompt="", messages=[], tools=[])
    config = AgentLoopConfig(model=_model(), convert_to_llm=default_convert_to_llm, max_turns=5)
    events = await _collect(
        [UserMessage(content="hi", timestamp=int(time.time() * 1000))],
        ctx,
        config,
        mock_text_stream,
    )
    assert events[-1].type == "agent_end"


@pytest.mark.asyncio
async def test_tool_timeout_produces_error_result():
    """#2: a hung tool must time out into an error tool result the LLM can see."""
    import asyncio

    async def hang(_id, params, signal, on_update) -> AgentToolResult:
        await asyncio.sleep(30)
        return AgentToolResult(content=[{"type": "text", "text": "never"}], details={})

    tool = SimpleTool(name="hang", description="", label="H", parameters={}, execute_fn=hang)
    ctx = AgentContext(system_prompt="", messages=[], tools=[tool])
    config = AgentLoopConfig(
        model=_model(), convert_to_llm=default_convert_to_llm, tool_timeout=0.1
    )

    t0 = time.monotonic()
    events = await _collect(
        [UserMessage(content="go", timestamp=int(time.time() * 1000))],
        ctx,
        config,
        _single_tool_stream("hang"),
    )
    elapsed = time.monotonic() - t0

    ends = [e for e in events if e.type == "tool_execution_end"]
    assert len(ends) == 1
    assert ends[0].is_error is True
    text = ends[0].result.content[0]["text"]
    assert "timed out" in text
    assert elapsed < 5


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
