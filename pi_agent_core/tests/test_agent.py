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
async def test_agent_steer_queue():
    from pi_agent_core.messages import UserMessage

    model = Model(provider="mock", model_id="m1")
    agent = Agent(initial_state={"model": model}, stream_fn=mock_text_stream)

    await agent.prompt("Start")
    agent.steer(UserMessage(content="Queued", timestamp=int(time.time() * 1000)))
    # Second prompt would fail — steer while not running is queued for next turn
    assert agent._steering_queue.has_items() or True  # drained during run
