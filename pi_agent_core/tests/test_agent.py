"""Agent class tests."""

from __future__ import annotations

import time

import pytest

from pi_agent_core.agent import Agent
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import Model


@pytest.mark.asyncio
async def test_agent_prompt_and_subscribe():
    events: list = []
    model = Model(provider="mock", model_id="m1")

    agent = Agent(
        initial_state={"system_prompt": "helpful", "model": model},
        stream_fn=mock_text_stream,
    )
    agent.subscribe(lambda e, s: events.append(e.type))

    await agent.prompt("Hello")
    await agent.wait_for_idle()

    assert not agent.is_streaming
    assert len(agent.messages) >= 2
    assert "agent_start" in events
    assert events[-1] == "agent_end"


@pytest.mark.asyncio
async def test_agent_prepare_next_turn_hook():
    """D2: the prepare_next_turn wrapper must accept the loop's context argument."""
    calls = {"n": 0}

    def prep(signal):
        calls["n"] += 1
        return None

    model = Model(provider="mock", model_id="m1")
    agent = Agent(
        initial_state={"model": model},
        stream_fn=mock_text_stream,
        prepare_next_turn=prep,
    )
    await agent.prompt("Hi")
    await agent.wait_for_idle()

    assert calls["n"] == 1
    assert agent.error_message is None


@pytest.mark.asyncio
async def test_agent_steer_queue():
    """Steering while idle stays queued; continue_() drains it as the next prompt."""
    from pi_agent_core.messages import UserMessage

    model = Model(provider="mock", model_id="m1")
    agent = Agent(initial_state={"model": model}, stream_fn=mock_text_stream)

    await agent.prompt("Start")
    await agent.wait_for_idle()

    agent.steer(UserMessage(content="Queued", timestamp=int(time.time() * 1000)))
    assert agent.has_queued_messages()

    await agent.continue_()
    await agent.wait_for_idle()

    assert not agent.has_queued_messages()
    user_contents = [
        str(getattr(m, "content", "")) for m in agent.messages if getattr(m, "role", None) == "user"
    ]
    assert any("Queued" in c for c in user_contents)


@pytest.mark.asyncio
async def test_agent_max_turns_sets_error_message():
    """#2: hitting max_turns surfaces as error_message, with a complete event tail."""
    from pi_agent_core.event_stream import AssistantMessageEventStream
    from pi_agent_core.tests.mock_stream import _base_partial
    from pi_agent_core.tools import SimpleTool
    from pi_agent_core.types import AgentToolResult, DoneEvent, StartEvent

    async def endless_stream(model, context, options=None):
        stream = AssistantMessageEventStream()
        partial = _base_partial(
            model,
            [{"type": "toolCall", "id": "c1", "name": "noop", "arguments": {}}],
        )
        partial.stopReason = "toolUse"
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream

    async def noop(_id, params, signal, on_update) -> AgentToolResult:
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    tool = SimpleTool(name="noop", description="", label="N", parameters={}, execute_fn=noop)
    model = Model(provider="mock", model_id="m1")
    agent = Agent(
        initial_state={"model": model, "tools": [tool]},
        stream_fn=endless_stream,
        max_turns=2,
    )

    events: list[str] = []
    agent.subscribe(lambda e, s: events.append(e.type))

    await agent.prompt("go")
    await agent.wait_for_idle()

    assert agent.error_message is not None
    assert "max_turns=2" in agent.error_message
    assert agent.is_streaming is False
    # settlement barrier still holds
    assert events[-1] == "agent_end"


@pytest.mark.asyncio
async def test_abort_signal_supports_event_wait():
    """B4: the internal abort signal exposes wait_aborted() for race-based cancellation."""
    import asyncio

    from pi_agent_core.agent import _AbortController

    controller = _AbortController()
    signal = controller.signal
    assert signal.aborted is False

    async def abort_soon():
        await asyncio.sleep(0.01)
        controller.abort()

    abort_task = asyncio.ensure_future(abort_soon())
    await asyncio.wait_for(signal.wait_aborted(), timeout=1)
    await abort_task
    assert signal.aborted is True
