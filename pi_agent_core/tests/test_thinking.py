"""Tests for thinking/reasoning parameter mapping and content ordering."""

from __future__ import annotations

from typing import get_args

import pytest

from pi_agent_core.adapters.langchain_stream import (
    _ANTHROPIC_BUDGET,
    _DEEPSEEK_EFFORT,
    _OPENAI_EFFORT,
    _apply_reasoning_params,
    _extract_thinking_delta,
)
from pi_agent_core.tests.mock_stream import mock_thinking_stream
from pi_agent_core.types import LlmContext, Model, ThinkingLevel


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


def test_apply_reasoning_max():
    """``max`` is the budget of ``xhigh`` on Anthropic, and "max" as it is in the OpenAI format."""
    anthropic = Model(provider="anthropic", model_id="claude-3", reasoning=True)
    kwargs = _apply_reasoning_params({}, anthropic, "max")
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 40000}
    assert kwargs["max_tokens"] > 40000

    openai = Model(provider="openai", model_id="gpt-5.6", reasoning=True)
    assert _apply_reasoning_params({}, openai, "max") == {"reasoning_effort": "max"}
    # The level below keeps its mapping: older OpenAI models take no "xhigh".
    assert _apply_reasoning_params({}, openai, "xhigh") == {"reasoning_effort": "high"}


def test_max_needs_the_capability_flag_like_every_level():
    openai = Model(provider="openai", model_id="gpt-4o", reasoning=False)
    assert _apply_reasoning_params({}, openai, "max") == {}


def test_every_thinking_level_has_a_key_in_each_table():
    """A level that a table lacks sends no parameter, so the model silently does not think."""
    levels = set(get_args(ThinkingLevel))
    for table in (_ANTHROPIC_BUDGET, _OPENAI_EFFORT, _DEEPSEEK_EFFORT):
        assert set(table) == levels
        assert [level for level, value in table.items() if value is None] == ["off"]


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
