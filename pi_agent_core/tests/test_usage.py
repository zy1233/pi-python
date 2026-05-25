"""Tests for usage extraction and cost_calculator."""

from __future__ import annotations

from types import SimpleNamespace

from pi_agent_core.adapters.langchain_stream import _apply_cost, _extract_usage
from pi_agent_core.messages import Usage, UsageCost
from pi_agent_core.types import Model, StreamOptions


def _chunk(meta: dict) -> SimpleNamespace:
    return SimpleNamespace(usage_metadata=meta)


def test_extract_usage_openai():
    chunk = _chunk(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_token_details": {"cache_read": 10, "cache_creation": 5},
            "output_token_details": {"reasoning": 20},
        }
    )
    usage = _extract_usage(chunk, "openai")
    assert usage.input == 100
    assert usage.output == 50
    assert usage.cacheRead == 10
    assert usage.cacheWrite == 5
    assert usage.totalTokens == 150
    assert usage.reasoningTokens == 20


def test_extract_usage_anthropic():
    chunk = _chunk(
        {
            "input_tokens": 200,
            "output_tokens": 80,
            "total_tokens": 280,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 15,
        }
    )
    usage = _extract_usage(chunk, "anthropic")
    assert usage.input == 200
    assert usage.output == 80
    assert usage.cacheRead == 30
    assert usage.cacheWrite == 15
    assert usage.totalTokens == 280


def test_cost_calculator_writes_cost():
    model = Model(provider="mock", model_id="m1")

    def calc(usage: Usage, m: Model) -> UsageCost:
        return UsageCost(input=0.01, output=0.02, total=0.03)

    usage = Usage(input=10, output=5)
    options = StreamOptions(cost_calculator=calc)
    result = _apply_cost(usage, model, options)
    assert result.cost.input == 0.01
    assert result.cost.output == 0.02
    assert result.cost.total == 0.03


def test_no_cost_calculator_zeros():
    model = Model(provider="mock", model_id="m1")
    usage = Usage(input=10, output=5)
    result = _apply_cost(usage, model, StreamOptions())
    assert result.cost.input == 0
    assert result.cost.output == 0
    assert result.cost.total == 0
