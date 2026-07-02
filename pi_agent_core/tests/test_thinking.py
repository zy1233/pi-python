"""Tests for thinking/reasoning parameter mapping and content ordering."""

from __future__ import annotations

import pytest

from pi_agent_core.adapters.langchain_stream import _apply_reasoning_params, _extract_thinking_delta
from pi_agent_core.tests.mock_stream import mock_thinking_stream
from pi_agent_core.types import LlmContext, Model


def test_apply_reasoning_anthropic():
    """Top-level params (not model_kwargs) with max_tokens headroom above the budget."""
    model = Model(provider="anthropic", model_id="claude-3", reasoning=True)
    kwargs = _apply_reasoning_params({}, model, "medium")
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 10000}
    assert kwargs["max_tokens"] > 10000
    assert "model_kwargs" not in kwargs


def test_apply_reasoning_openai():
    model = Model(provider="openai", model_id="o3-mini", reasoning=True)
    kwargs = _apply_reasoning_params({}, model, "high")
    assert kwargs["reasoning_effort"] == "high"
    assert "model_kwargs" not in kwargs


def test_apply_reasoning_off():
    model = Model(provider="openai", model_id="gpt-4", reasoning=True)
    assert _apply_reasoning_params({}, model, "off") == {}
    assert _apply_reasoning_params({}, model, None) == {}


def test_apply_reasoning_requires_capability_flag():
    """D5: models without reasoning capability must not receive reasoning params."""
    openai_model = Model(provider="openai", model_id="gpt-4o", reasoning=False)
    assert _apply_reasoning_params({}, openai_model, "high") == {}
    anthropic_model = Model(provider="anthropic", model_id="claude-3", reasoning=False)
    assert _apply_reasoning_params({}, anthropic_model, "medium") == {}


def test_apply_reasoning_keeps_explicit_max_tokens():
    model = Model(provider="anthropic", model_id="claude-3", reasoning=True)
    kwargs = _apply_reasoning_params({"max_tokens": 64000}, model, "low")
    assert kwargs["max_tokens"] == 64000


def test_extract_thinking_delta_from_blocks():
    content = [
        {"type": "thinking", "thinking": "step one"},
        {"type": "text", "text": "visible"},
    ]
    assert _extract_thinking_delta(content) == "step one"


@pytest.mark.asyncio
async def test_mock_thinking_stream_order():
    model = Model(provider="anthropic", model_id="claude-3", reasoning=True)
    stream = await mock_thinking_stream(model, LlmContext(system_prompt=None, messages=[]))
    final = await stream.message_result()
    assert len(final.content) >= 2
    assert final.content[0]["type"] == "thinking"
    assert final.content[1]["type"] == "text"
