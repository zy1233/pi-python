"""Tests for thinking/reasoning parameter mapping and content ordering."""

from __future__ import annotations

from typing import get_args

import pytest

from pi_agent_core.adapters.langchain_stream import (
    _ANTHROPIC_BUDGET,
    _DEEPSEEK_EFFORT,
    _GEMINI_THINKING_LEVEL,
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
    for table in (_ANTHROPIC_BUDGET, _OPENAI_EFFORT, _DEEPSEEK_EFFORT, _GEMINI_THINKING_LEVEL):
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


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("minimal", "minimal"),
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "high"),
        ("max", "high"),
    ],
)
def test_apply_reasoning_gemini_explicit_format(level: ThinkingLevel, expected: str):
    model = Model(
        provider="openai",
        model_id="google/models/gemini-4-hydrogen",
        base_url="http://127.0.0.1:3425/v1",
        reasoning=True,
        thinking_format="gemini",
    )
    kwargs = _apply_reasoning_params({}, model, level)
    assert "reasoning_effort" not in kwargs
    assert kwargs == {
        "extra_body": {
            "extra_body": {
                "google": {
                    "thinking_config": {
                        "include_thoughts": True,
                        "thinking_level": expected,
                    }
                }
            }
        }
    }


def test_apply_reasoning_gemini_off_and_disabled():
    model = Model(
        provider="openai",
        model_id="google/models/gemini-4-hydrogen",
        base_url="http://127.0.0.1:3425/v1",
        reasoning=True,
        thinking_format="gemini",
    )
    assert _apply_reasoning_params({}, model, "off") == {}
    assert _apply_reasoning_params({}, model, None) == {}

    disabled = Model(
        provider="openai",
        model_id="google/models/gemini-4-hydrogen",
        base_url="http://127.0.0.1:3425/v1",
        reasoning=False,
        thinking_format="gemini",
    )
    assert _apply_reasoning_params({}, disabled, "high") == {}


@pytest.mark.parametrize(
    "base_url",
    [
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/p/locations/us-central1/endpoints/openapi",
    ],
)
def test_apply_reasoning_google_hosts_auto_select_gemini(base_url: str):
    model = Model(
        provider="openai",
        model_id="gemini-3-pro",
        base_url=base_url,
        reasoning=True,
    )
    kwargs = _apply_reasoning_params({}, model, "high")
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["extra_body"]["google"]["thinking_config"] == {
        "include_thoughts": True,
        "thinking_level": "high",
    }


@pytest.mark.parametrize(
    "model_id",
    [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "models/gemini-2.5-pro",
        "google/gemini-2.5-flash-preview",
    ],
)
def test_apply_reasoning_google_hosts_keep_openai_for_gemini_2_5(model_id: str):
    """Gemini 2.5 does not take thinking_level, so auto-detect keeps reasoning_effort."""
    model = Model(
        provider="openai",
        model_id=model_id,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        reasoning=True,
    )
    kwargs = _apply_reasoning_params({}, model, "high")
    assert "extra_body" not in kwargs
    assert kwargs == {"reasoning_effort": "high"}


def test_apply_reasoning_explicit_gemini_overrides_gemini_2_5():
    model = Model(
        provider="openai",
        model_id="gemini-2.5-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        reasoning=True,
        thinking_format="gemini",
    )
    kwargs = _apply_reasoning_params({}, model, "xhigh")
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["extra_body"]["google"]["thinking_config"] == {
        "include_thoughts": True,
        "thinking_level": "high",
    }


def test_apply_reasoning_explicit_openai_overrides_google_host():
    model = Model(
        provider="openai",
        model_id="gemini-3-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        reasoning=True,
        thinking_format="openai",
    )
    assert _apply_reasoning_params({}, model, "high") == {"reasoning_effort": "high"}


def test_apply_reasoning_openrouter_gemini_stays_openai_when_unset():
    model = Model(
        provider="openai",
        model_id="google/gemini-2.5-pro",
        base_url="https://openrouter.ai/api/v1",
        reasoning=True,
    )
    assert _apply_reasoning_params({}, model, "high") == {"reasoning_effort": "high"}
