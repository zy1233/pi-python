"""Integration tests for mock usage stream and Agent cost_calculator."""

from __future__ import annotations

import pytest

from pi_agent_core.agent import Agent
from pi_agent_core.messages import Usage, UsageCost
from pi_agent_core.tests.mock_stream import mock_usage_stream
from pi_agent_core.types import LlmContext, Model


@pytest.mark.asyncio
async def test_mock_usage_stream_fills_usage():
    model = Model(provider="mock", model_id="m1")
    stream = await mock_usage_stream(model, LlmContext(system_prompt=None, messages=[]))
    final = await stream.message_result()
    assert final.usage.input == 42
    assert final.usage.output == 21
    assert final.usage.reasoningTokens == 7
    assert final.usage.cost.total == 0


@pytest.mark.asyncio
async def test_agent_cost_calculator():
    model = Model(provider="mock", model_id="m1")

    def calc(usage: Usage, m: Model) -> UsageCost:
        return UsageCost(input=1.0, output=2.0, total=3.0)

    agent = Agent(
        initial_state={"model": model},
        stream_fn=mock_usage_stream,
        cost_calculator=calc,
    )
    await agent.prompt("Hi")
    await agent.wait_for_idle()
    assistant = next(m for m in agent.messages if getattr(m, "role", None) == "assistant")
    assert assistant.usage.cost.total == 3.0
