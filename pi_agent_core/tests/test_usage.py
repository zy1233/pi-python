"""Tests for usage accumulation and cost_calculator (audit fix B2)."""

from __future__ import annotations

from types import SimpleNamespace

from pi_agent_core.adapters.langchain_stream import (
    _apply_cost,
    _merge_usage_meta,
    _usage_from_meta,
)
from pi_agent_core.messages import Usage, UsageCost
from pi_agent_core.types import Model, StreamOptions


def test_usage_from_meta_standardized_fields():
    """Cache/reasoning tokens use LangChain-normalized detail fields for every provider."""
    acc: dict = {}
    _merge_usage_meta(
        acc,
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_token_details": {"cache_read": 10, "cache_creation": 5},
            "output_token_details": {"reasoning": 20},
        },
    )
    usage = _usage_from_meta(acc)
    assert usage.input == 100
    assert usage.output == 50
    assert usage.cacheRead == 10
    assert usage.cacheWrite == 5
    assert usage.totalTokens == 150
    assert usage.reasoningTokens == 20


def test_usage_accumulates_across_chunks():
    """Complementary splits (Anthropic): input on first chunk, output on last."""
    acc: dict = {}
    _merge_usage_meta(
        acc,
        {
            "input_tokens": 200,
            "output_tokens": 0,
            "total_tokens": 200,
            "input_token_details": {"cache_read": 30, "cache_creation": 15},
        },
    )
    _merge_usage_meta(acc, {"input_tokens": 0, "output_tokens": 80, "total_tokens": 80})
    usage = _usage_from_meta(acc)
    assert usage.input == 200
    assert usage.output == 80
    assert usage.totalTokens == 280
    assert usage.cacheRead == 30
    assert usage.cacheWrite == 15


def test_usage_cumulative_snapshots_not_summed():
    """Cumulative snapshots (SiliconFlow/vLLM gateways) must not be inflated.

    Real-API smoke exposed this: such providers report usage on every chunk as
    a running total (output=1,2,3,...); summing produced ~18k tokens for a
    ~100-token reply. Per-field max is correct for every observed shape.
    """
    acc: dict = {}
    for out in (1, 2, 3):
        _merge_usage_meta(
            acc,
            {
                "input_tokens": 12,
                "output_tokens": out,
                "total_tokens": 12 + out,
                "output_token_details": {"reasoning": max(0, out - 1)},
            },
        )
    usage = _usage_from_meta(acc)
    assert usage.input == 12
    assert usage.output == 3
    assert usage.totalTokens == 15
    assert usage.reasoningTokens == 2


def test_merge_usage_meta_accepts_object_chunks():
    """usage_metadata may arrive as attribute-style objects instead of dicts."""
    acc: dict = {}
    meta = SimpleNamespace(input_tokens=7, output_tokens=3, total_tokens=10)
    _merge_usage_meta(acc, meta)
    usage = _usage_from_meta(acc)
    assert usage.input == 7
    assert usage.output == 3
    assert usage.totalTokens == 10


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
