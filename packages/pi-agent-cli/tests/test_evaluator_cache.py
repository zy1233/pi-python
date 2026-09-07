"""Tests for benchmark evaluator cache hit rate calculation."""

from __future__ import annotations

from pi_agent_cli.benchmarks.evaluator import calculate_cache_hit_rate


def test_cache_hit_rate_deepseek_openai_semantics():
    """For DeepSeek/OpenAI, input_tokens includes cached_tokens (input = uncached + cached)."""
    # 50% cached
    rate = calculate_cache_hit_rate(total_input=200, total_cached=100, provider="deepseek")
    assert rate == 0.5

    # 100% cached
    rate = calculate_cache_hit_rate(total_input=100, total_cached=100, provider="openai")
    assert rate == 1.0

    # 0% cached
    rate = calculate_cache_hit_rate(total_input=100, total_cached=0, provider="deepseek")
    assert rate == 0.0


def test_cache_hit_rate_real_trial_data():
    """Verify on real trial data from sqlite-db-truncate benchmark.

    Previously, the bugged formula `cached / (input + cached)` reported 43.78%.
    The corrected formula `cached / input` reports 77.87%, aligning with Pi's ~79.4%.
    """
    total_input = 189691
    total_cached = 147712

    # Old bugged calculation
    bugged_rate = total_cached / (total_input + total_cached)
    assert round(bugged_rate, 4) == 0.4378

    # Correct calculation
    corrected_rate = calculate_cache_hit_rate(
        total_input=total_input,
        total_cached=total_cached,
        provider="deepseek",
    )
    assert round(corrected_rate, 4) == 0.7787


def test_cache_hit_rate_anthropic_semantics():
    """For Anthropic, input_tokens reports uncached tokens only; total = input + cached."""
    # 100 uncached + 100 cached -> 50% hit rate
    rate = calculate_cache_hit_rate(total_input=100, total_cached=100, provider="anthropic")
    assert rate == 0.5

    # 0 cached
    rate = calculate_cache_hit_rate(total_input=100, total_cached=0, provider="anthropic")
    assert rate == 0.0


def test_cache_hit_rate_edge_cases():
    """Zero inputs, negative values, and overflow protection."""
    # Zero input
    assert calculate_cache_hit_rate(total_input=0, total_cached=0, provider="deepseek") == 0.0
    assert calculate_cache_hit_rate(total_input=0, total_cached=0, provider="anthropic") == 0.0

    # Cached exceeds input (anomalous gateway snapshot) capped at 1.0
    rate = calculate_cache_hit_rate(total_input=100, total_cached=150, provider="deepseek")
    assert rate == 1.0
