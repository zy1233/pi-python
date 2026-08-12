"""Real LLM integration tests using SiliconFlow DeepSeek-V4-Flash.

These tests hit a live API and are skipped when the API key is not set.
Run with (PowerShell):
    $env:REAL_LLM_API_KEY = 'sk-...'
    .venv-test-real\\Scripts\\python.exe -m pytest -m real_llm -v

Environment variables:
    REAL_LLM_API_KEY   — (required) SiliconFlow API key
    REAL_LLM_BASE_URL  — defaults to https://api.siliconflow.cn/v1
    REAL_LLM_MODEL     — defaults to deepseek-ai/DeepSeek-V4-Flash
    REAL_LLM_PROVIDER  — defaults to deepseek
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from pydantic import BaseModel, Field

from pi_agent_core import AgentContext, AgentLoopConfig, Model, run_agent_loop
from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.messages import UserMessage
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult

BASE_URL = os.environ.get("REAL_LLM_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.environ.get("REAL_LLM_API_KEY", "")
MODEL_ID = os.environ.get("REAL_LLM_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
PROVIDER = os.environ.get("REAL_LLM_PROVIDER", "deepseek")

pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(not API_KEY, reason="REAL_LLM_API_KEY not set"),
]


def _model(**overrides) -> Model:
    return Model(
        provider=PROVIDER,
        model_id=MODEL_ID,
        base_url=BASE_URL,
        context_window=32_000,
        **overrides,
    )


def _config(**overrides) -> AgentLoopConfig:
    return AgentLoopConfig(
        model=_model(),
        convert_to_llm=default_convert_to_llm,
        api_key=API_KEY,
        **overrides,
    )


async def _run_and_collect(
    prompt_text: str, *, system_prompt: str = "Be concise.", tools=None, config_overrides=None
):
    """Helper: run a single prompt through the loop, return (messages, events)."""
    events: list = []

    async def emit(e) -> None:
        events.append(e)

    prompt = UserMessage(content=prompt_text)
    ctx = AgentContext(system_prompt=system_prompt, messages=[], tools=tools or [])
    cfg = _config(**(config_overrides or {}))
    messages = await run_agent_loop([prompt], ctx, cfg, emit)
    return messages, events


# ---------------------------------------------------------------------------
# Test 1: Basic text streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_stream_basic():
    """LLM returns streaming text with correct event sequence and usage."""
    messages, events = await _run_and_collect("Say hello in one sentence.")

    types = [e.type for e in events]
    updates = [e.assistant_message_event.type for e in events if e.type == "message_update"]
    final = messages[-1]

    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert "text_delta" in updates
    assert "text_start" in updates
    assert "text_end" in updates

    text_blocks = [b for b in final.content if b.get("type") == "text"]
    assert text_blocks and text_blocks[0]["text"].strip()
    assert final.stopReason == "stop"
    assert final.usage.input > 0
    assert final.usage.output > 0


# ---------------------------------------------------------------------------
# Test 2: Multi-turn conversation context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_turn_context():
    """LLM correctly references prior conversation context."""
    events: list = []

    async def emit(e) -> None:
        events.append(e)

    turn1_prompt = UserMessage(content="My name is TestUser123. Remember it.")
    ctx = AgentContext(system_prompt="Be concise.", messages=[], tools=[])
    cfg = _config()
    turn1_messages = await run_agent_loop([turn1_prompt], ctx, cfg, emit)

    events.clear()
    turn2_prompt = UserMessage(content="What is my name?")
    ctx2 = AgentContext(system_prompt="Be concise.", messages=turn1_messages, tools=[])
    turn2_messages = await run_agent_loop([turn2_prompt], ctx2, cfg, emit)

    final = turn2_messages[-1]
    final_text = " ".join(b["text"] for b in final.content if b.get("type") == "text")
    assert "TestUser123" in final_text


# ---------------------------------------------------------------------------
# Test 3: Single tool call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_tool_call():
    """LLM invokes a single tool and incorporates the result."""

    class CalcParams(BaseModel):
        expression: str = Field(description="Math expression to evaluate")

    calls: list[str] = []

    async def calc_execute(_id, params: CalcParams, signal, on_update):
        calls.append(params.expression)
        return AgentToolResult(
            content=[{"type": "text", "text": "42"}],
            details={},
        )

    tool = SimpleTool(
        name="calculator",
        description="Evaluate a math expression and return the result",
        label="Calculator",
        parameters=CalcParams,
        execute_fn=calc_execute,
    )

    messages, events = await _run_and_collect(
        "What is 6 * 7? You must use the calculator tool.",
        tools=[tool],
        config_overrides={"max_turns": 4},
    )

    types = [e.type for e in events]
    updates = [e.assistant_message_event.type for e in events if e.type == "message_update"]

    assert calls, "Tool was never called"
    assert "tool_execution_start" in types
    assert "tool_execution_end" in types
    assert "toolcall_start" in updates
    assert "toolcall_end" in updates

    final = messages[-1]
    final_text = " ".join(b["text"] for b in final.content if b.get("type") == "text")
    assert "42" in final_text


# ---------------------------------------------------------------------------
# Test 4: Multiple tool calls in sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_tool_sequence():
    """LLM can call tools across multiple turns to compose an answer."""

    class CityInfoParams(BaseModel):
        city: str = Field(description="City name")

    results_map = {
        "beijing": "Population: 21M, Country: China",
        "tokyo": "Population: 14M, Country: Japan",
    }
    calls: list[str] = []

    async def city_info(_id, params: CityInfoParams, signal, on_update):
        key = params.city.lower()
        calls.append(key)
        text = results_map.get(key, f"Unknown city: {params.city}")
        return AgentToolResult(content=[{"type": "text", "text": text}], details={})

    tool = SimpleTool(
        name="city_info",
        description="Get info about a city (population, country)",
        label="CityInfo",
        parameters=CityInfoParams,
        execute_fn=city_info,
    )

    messages, _events = await _run_and_collect(
        "Compare the populations of Beijing and Tokyo. "
        "Call city_info for each city separately, then summarize.",
        system_prompt="Use tools when asked. Call city_info once per city.",
        tools=[tool],
        config_overrides={"max_turns": 6},
    )

    assert len(calls) >= 2, f"Expected at least 2 tool calls, got {calls}"

    final = messages[-1]
    assert final.stopReason == "stop"
    final_text = " ".join(b["text"] for b in final.content if b.get("type") == "text").lower()
    assert "21" in final_text or "beijing" in final_text


# ---------------------------------------------------------------------------
# Test 5: Structured output (JSON schema)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_output():
    """response_schema forces output into a validated Pydantic model."""

    class MovieReview(BaseModel):
        title: str = Field(description="Movie title")
        rating: int = Field(description="Rating from 1 to 10")
        summary: str = Field(description="One-sentence summary")

    messages, _events = await _run_and_collect(
        "Write a brief review of the movie 'Inception'.",
        config_overrides={"response_schema": MovieReview},
    )

    final = messages[-1]
    so = final.structured_output
    assert isinstance(so, dict), f"Expected dict, got {type(so)}"
    assert "title" in so
    assert "rating" in so
    assert isinstance(so["rating"], int)
    assert 1 <= so["rating"] <= 10
    assert "summary" in so


# ---------------------------------------------------------------------------
# Test 6: Abort mid-stream
# ---------------------------------------------------------------------------


class _Signal:
    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()

    async def wait_aborted(self) -> None:
        await self._event.wait()


@pytest.mark.asyncio
async def test_abort_mid_stream():
    """Aborting mid-stream produces stopReason=aborted promptly."""
    events: list = []

    async def emit(e) -> None:
        events.append(e)

    signal = _Signal()
    prompt = UserMessage(content="Count from 1 to 500, one number per line.")
    ctx = AgentContext(system_prompt="", messages=[], tools=[])
    cfg = _config()

    async def run():
        return await run_agent_loop([prompt], ctx, cfg, emit, signal=signal)

    task = asyncio.create_task(run())
    await asyncio.sleep(2.0)
    signal.abort()

    t0 = time.monotonic()
    messages = await task
    latency = time.monotonic() - t0

    final = messages[-1]
    assert final.stopReason == "aborted"
    assert latency < 5.0, f"Abort took too long: {latency:.2f}s"
    assert events[-1].type == "agent_end"


# ---------------------------------------------------------------------------
# Test 7: Usage / token counting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_token_counting():
    """Usage reports non-zero input and output tokens."""
    messages, _events = await _run_and_collect("What is 2+2? Reply with just the number.")

    final = messages[-1]
    assert final.usage.input > 0, "input tokens should be > 0"
    assert final.usage.output > 0, "output tokens should be > 0"
    assert final.usage.totalTokens >= final.usage.input + final.usage.output


# ---------------------------------------------------------------------------
# Test 8: System prompt adherence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_adherence():
    """LLM respects the system prompt instructions."""
    messages, _events = await _run_and_collect(
        "Tell me about Python.",
        system_prompt="You must respond in exactly 3 words. No more, no less.",
    )

    final = messages[-1]
    final_text = " ".join(b["text"] for b in final.content if b.get("type") == "text").strip()
    word_count = len(final_text.split())
    assert word_count <= 10, f"Expected short response, got {word_count} words: {final_text!r}"


# ---------------------------------------------------------------------------
# Test 9: Empty tool result handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_tool_result():
    """LLM gracefully handles a tool returning empty content."""

    class PingParams(BaseModel):
        target: str = Field(description="Target to ping")

    async def ping_execute(_id, params: PingParams, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": ""}], details={})

    tool = SimpleTool(
        name="ping",
        description="Ping a target (returns empty on success)",
        label="Ping",
        parameters=PingParams,
        execute_fn=ping_execute,
    )

    messages, _events = await _run_and_collect(
        "Ping google.com using the ping tool, then confirm it succeeded.",
        tools=[tool],
        config_overrides={"max_turns": 4},
    )

    final = messages[-1]
    assert final.stopReason == "stop"


# ---------------------------------------------------------------------------
# Test 10: Long output generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_output():
    """LLM can generate longer outputs with many text deltas."""
    messages, events = await _run_and_collect(
        "List the first 20 prime numbers, one per line with a brief explanation of each."
    )

    updates = [e.assistant_message_event.type for e in events if e.type == "message_update"]
    delta_count = updates.count("text_delta")

    assert delta_count > 5, f"Expected many text deltas, got {delta_count}"

    final = messages[-1]
    final_text = " ".join(b["text"] for b in final.content if b.get("type") == "text")
    assert "2" in final_text and "7" in final_text
