"""Real-API smoke test for the LangChain adapter path (no mocks).

Exercises the paths the unit suite can only fake: real streaming chunk shapes,
tool-call chunk splitting, usage metadata, abort mid-stream, and (as an
observation) reasoning/thinking output of OpenAI-compatible providers.

Usage (PowerShell):
    $env:SMOKE_BASE_URL = 'https://api.siliconflow.cn/v1'
    $env:SMOKE_API_KEY  = 'sk-...'
    $env:SMOKE_MODEL    = 'Qwen/Qwen3-8B'
    python scripts/smoke_real_api.py

The API key is read from the environment only; never hardcode it here.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic import BaseModel, Field

from pi_agent_core import AgentContext, AgentLoopConfig, Model, run_agent_loop
from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.messages import UserMessage
from pi_agent_core.tools import SimpleTool

BASE_URL = os.environ.get("SMOKE_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.environ.get("SMOKE_API_KEY") or os.environ.get("SILICONFLOW_API_KEY") or ""
MODEL_ID = os.environ.get("SMOKE_MODEL", "Qwen/Qwen3-8B")
# "deepseek" preserves reasoning_content thinking on OpenAI-compatible
# gateways; set SMOKE_PROVIDER=openai to exercise the plain ChatOpenAI path.
PROVIDER = os.environ.get("SMOKE_PROVIDER", "deepseek")


def _model() -> Model:
    return Model(provider=PROVIDER, model_id=MODEL_ID, base_url=BASE_URL, context_window=32_000)


def _config(**overrides) -> AgentLoopConfig:
    return AgentLoopConfig(
        model=_model(),
        convert_to_llm=default_convert_to_llm,
        api_key=API_KEY,
        **overrides,
    )


def _report(name: str, checks: dict[str, bool], notes: list[str] | None = None) -> bool:
    ok = all(checks.values())
    print(f"\n=== {name}: {'PASS' if ok else 'FAIL'} ===")
    for label, passed in checks.items():
        print(f"  [{'x' if passed else ' '}] {label}")
    for note in notes or []:
        print(f"  note: {note}")
    return ok


async def smoke_text_stream() -> bool:
    """Plain text: event sequence, granular boundaries, usage, correlation ids."""
    events: list = []

    async def emit(e) -> None:
        events.append(e)

    prompt = UserMessage(content="Reply with one short English sentence about the sea.")
    ctx = AgentContext(system_prompt="Be terse.", messages=[], tools=[])
    messages = await run_agent_loop([prompt], ctx, _config(), emit)

    final = messages[-1]
    types = [e.type for e in events]
    updates = [e.assistant_message_event.type for e in events if e.type == "message_update"]
    text_blocks = [b for b in final.content if b.get("type") == "text"]
    thinking_blocks = [b for b in final.content if b.get("type") == "thinking"]

    checks = {
        "agent_start first / agent_end last": types[0] == "agent_start"
        and types[-1] == "agent_end",
        "text deltas streamed": "text_delta" in updates,
        "text_start/text_end boundaries": "text_start" in updates and "text_end" in updates,
        "final text non-empty": bool(text_blocks and text_blocks[0]["text"].strip()),
        "stopReason == stop": final.stopReason == "stop",
        "usage input/output non-zero": final.usage.input > 0 and final.usage.output > 0,
        "run_id stamped on all events": all(e.run_id for e in events),
    }
    notes = [
        f"usage: input={final.usage.input} output={final.usage.output} "
        f"total={final.usage.totalTokens} reasoning={final.usage.reasoningTokens}",
        f"thinking captured by adapter: {'yes' if thinking_blocks else 'no'} "
        f"(thinking_delta events: {updates.count('thinking_delta')})",
    ]
    return _report("text stream", checks, notes)


async def smoke_tool_loop() -> bool:
    """Tool loop: real tool_call chunks -> execution -> follow-up turn."""

    class WeatherParams(BaseModel):
        city: str = Field(description="City name")

    calls: list[str] = []

    async def get_weather(_id, params: WeatherParams, signal, on_update):
        calls.append(params.city)
        from pi_agent_core import AgentToolResult

        return AgentToolResult(
            content=[{"type": "text", "text": f"Sunny, 25C in {params.city}"}],
            details={},
        )

    tool = SimpleTool(
        name="get_weather",
        description="Get current weather for a city",
        label="Weather",
        parameters=WeatherParams,
        execute_fn=get_weather,
    )

    events: list = []

    async def emit(e) -> None:
        events.append(e)

    prompt = UserMessage(
        content="What's the weather in Beijing right now? You must call get_weather."
    )
    ctx = AgentContext(system_prompt="Use tools when asked.", messages=[], tools=[tool])
    messages = await run_agent_loop([prompt], ctx, _config(max_turns=4), emit)

    types = [e.type for e in events]
    updates = [e.assistant_message_event.type for e in events if e.type == "message_update"]
    tool_use_msgs = [m for m in messages if getattr(m, "stopReason", None) == "toolUse"]
    final = messages[-1]
    final_text = " ".join(
        b["text"] for b in getattr(final, "content", []) if b.get("type") == "text"
    )

    checks = {
        "assistant issued toolUse": bool(tool_use_msgs),
        "toolcall_start/end events": "toolcall_start" in updates and "toolcall_end" in updates,
        "tool executed with parsed args": calls == ["Beijing"],
        "tool_execution start+end events": "tool_execution_start" in types
        and "tool_execution_end" in types,
        ">= 2 turns (tool turn + answer turn)": types.count("turn_start") >= 2,
        "final answer mentions weather": any(
            w in final_text.lower() for w in ("sunny", "25", "beijing")
        ),
        "final stopReason == stop": final.stopReason == "stop",
    }
    notes = [f"turns: {types.count('turn_start')}, final: {final_text[:120]!r}"]
    return _report("tool loop", checks, notes)


async def smoke_structured_output() -> bool:
    """response_schema: native response_format + prompt injection + parsing."""

    class Person(BaseModel):
        name: str = Field(description="Full name")
        age: int = Field(description="Age in years")
        city: str = Field(description="Home city")

    events: list = []

    async def emit(e) -> None:
        events.append(e)

    prompt = UserMessage(content="Invent a fictional person from Shanghai in her thirties.")
    ctx = AgentContext(system_prompt="You extract structured data.", messages=[], tools=[])
    messages = await run_agent_loop([prompt], ctx, _config(response_schema=Person), emit)

    final = messages[-1]
    so = final.structured_output
    checks = {
        "structured_output parsed": isinstance(so, dict),
        "schema fields present": isinstance(so, dict) and {"name", "age", "city"} <= set(so),
        "age is int": isinstance(so, dict) and isinstance(so.get("age"), int),
        "stopReason == stop": final.stopReason == "stop",
    }
    return _report("structured output", checks, [f"parsed: {so}"])


class _Signal:
    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()

    async def wait_aborted(self) -> None:
        await self._event.wait()


async def smoke_abort() -> bool:
    """Abort mid-stream must yield stopReason == aborted quickly."""
    events: list = []

    async def emit(e) -> None:
        events.append(e)

    signal = _Signal()
    prompt = UserMessage(content="Count slowly from 1 to 300, one number per line.")
    ctx = AgentContext(system_prompt="", messages=[], tools=[])

    async def run() -> list:
        return await run_agent_loop([prompt], ctx, _config(), emit, signal=signal)

    task = asyncio.create_task(run())
    await asyncio.sleep(2.0)
    signal.abort()
    import time as _time

    t0 = _time.monotonic()
    messages = await task
    abort_latency = _time.monotonic() - t0

    final = messages[-1]
    checks = {
        "stopReason == aborted": final.stopReason == "aborted",
        "run ended within 5s of abort": abort_latency < 5.0,
        "agent_end emitted": events[-1].type == "agent_end",
    }
    return _report("abort", checks, [f"latency after abort: {abort_latency:.2f}s"])


async def observe_raw_reasoning() -> None:
    """Observation only: where does this provider put reasoning in raw chunks?"""
    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(model=MODEL_ID, api_key=API_KEY, base_url=BASE_URL, max_tokens=64)
    content_shapes: set[str] = set()
    ak_keys: set[str] = set()
    async for chunk in chat.astream("Briefly: why is the sky blue?"):
        content_shapes.add(type(chunk.content).__name__)
        ak_keys.update(k for k, v in (chunk.additional_kwargs or {}).items() if v)
    print("\n=== raw reasoning observation ===")
    print(f"  chunk.content types seen: {sorted(content_shapes)}")
    print(f"  additional_kwargs keys with values: {sorted(ak_keys)}")


async def main() -> int:
    if not API_KEY:
        print("SMOKE_API_KEY (or SILICONFLOW_API_KEY) is not set; aborting.")
        return 2
    print(f"target: {BASE_URL} model={MODEL_ID}")

    results = [
        await smoke_text_stream(),
        await smoke_tool_loop(),
        await smoke_structured_output(),
        await smoke_abort(),
    ]
    await observe_raw_reasoning()

    print(f"\n{'ALL PASS' if all(results) else 'FAILURES PRESENT'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
