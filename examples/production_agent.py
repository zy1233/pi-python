"""Production-configured agent: hooks, budget signal, retries, structured output.

Runs against any OpenAI-compatible endpoint, or fully offline with a mock:

    PI_USE_MOCK=1 python3 examples/production_agent.py

    # real endpoint (e.g. SiliconFlow):
    PI_BASE_URL=https://api.siliconflow.cn/v1 \
    PI_API_KEY=sk-... \
    PI_MODEL=Qwen/Qwen3-8B \
    python3 examples/production_agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic import BaseModel, Field

from pi_agent_core import Agent, AgentToolResult, ContextBudget, Model
from pi_agent_core.tools import SimpleTool

USE_MOCK = os.environ.get("PI_USE_MOCK", "0") == "1"


class WeatherParams(BaseModel):
    city: str = Field(description="City name")


async def get_weather(_id, params: WeatherParams, signal, on_update) -> AgentToolResult:
    return AgentToolResult(
        content=[{"type": "text", "text": f"Sunny, 25C in {params.city}"}],
        details={},
    )


class EchoParams(BaseModel):
    message: str = Field(description="Text to echo")


async def echo(_id, params: EchoParams, signal, on_update) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": params.message}], details={})


class WeatherReport(BaseModel):
    city: str
    condition: str
    temperature_c: int


def build_agent() -> Agent:
    if USE_MOCK:
        from pi_agent_core.tests.mock_stream import mock_tool_stream

        stream_fn = mock_tool_stream
        model = Model(provider="mock", model_id="mock-1")
        api_key = None
    else:
        from pi_agent_core.adapters import langchain_stream

        stream_fn = langchain_stream
        # provider="deepseek" preserves reasoning_content thinking on
        # OpenAI-compatible gateways; plain "openai" works too.
        model = Model(
            provider=os.environ.get("PI_PROVIDER", "deepseek"),
            model_id=os.environ.get("PI_MODEL", "Qwen/Qwen3-8B"),
            base_url=os.environ.get("PI_BASE_URL", "https://api.siliconflow.cn/v1"),
            context_window=32_000,
        )
        api_key = os.environ.get("PI_API_KEY")

    # --- observability hooks ---
    def on_payload(payload: dict) -> None:
        print(
            f"[payload] model={payload['model']} messages={len(payload['messages'])} "
            f"tools={len(payload['tools'])}"
        )

    def on_response(message) -> None:
        u = message.usage
        print(f"\n[response] stop={message.stopReason} tokens={u.input}+{u.output}")

    # --- guardrail hooks ---
    async def before_llm_call(context, budget: ContextBudget | None):
        if budget is not None:
            print(
                f"[budget] {budget.used_tokens}/{budget.context_window} "
                f"({budget.fraction:.1%} of context window)"
            )
            # A real harness would compact here, e.g.:
            # if budget.fraction > 0.8: return await compact(context)
        return None

    def after_llm_call(context, message) -> None:
        # Raise here to abort the run (guardrail tripwire).
        pass

    def on_agent_end(messages) -> None:
        print(f"[end] run produced {len(messages)} messages")

    tools = [
        SimpleTool(
            name="get_weather",
            description="Get current weather for a city",
            label="Weather",
            parameters=WeatherParams,
            execute_fn=get_weather,
        ),
        # mock_tool_stream always calls "echo"; harmless for real models
        SimpleTool(
            name="echo",
            description="Echo text back",
            label="Echo",
            parameters=EchoParams,
            execute_fn=echo,
        ),
    ]

    return Agent(
        initial_state={
            "system_prompt": "Answer using tools when relevant.",
            "model": model,
            "tools": tools,
        },
        stream_fn=stream_fn,
        get_api_key=(lambda _provider: api_key) if api_key else None,
        # runaway protection + retries
        max_turns=8,
        tool_timeout=60.0,
        max_retries=3,
        # observability + guardrails
        on_payload=on_payload,
        on_response=on_response,
        before_llm_call=before_llm_call,
        after_llm_call=after_llm_call,
        on_agent_end=on_agent_end,
        # structured final answer (skipped by the mock stream)
        response_schema=None if USE_MOCK else WeatherReport,
    )


async def main() -> None:
    agent = build_agent()

    def on_event(event, signal):
        if event.type == "message_update":
            ame = event.assistant_message_event
            if ame.type == "text_delta":
                sys.stdout.write(ame.delta)
                sys.stdout.flush()
            elif ame.type == "thinking_start":
                sys.stdout.write("[thinking...] ")
                sys.stdout.flush()
        elif event.type == "tool_execution_start":
            print(f"\n[tool] {event.tool_name}({event.args}) (turn {event.turn_id})")

    agent.subscribe(on_event)
    await agent.prompt("What's the weather in Beijing? Use the tool, then summarize.")
    await agent.wait_for_idle()

    final = agent.messages[-1]
    if getattr(final, "structured_output", None) is not None:
        print(f"[structured] {final.structured_output}")
    if agent.error_message:
        print(f"[error] {agent.error_message}")


if __name__ == "__main__":
    asyncio.run(main())
