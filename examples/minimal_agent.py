"""Minimal agent example — requires API key for your provider."""

from __future__ import annotations

import asyncio
import os
import sys

from pi_agent_core import Agent, Model

# Use mock stream when PI_USE_MOCK=1 (no API key)
USE_MOCK = os.environ.get("PI_USE_MOCK", "0") == "1"


async def main() -> None:
    if USE_MOCK:
        from pi_agent_core.tests.mock_stream import mock_text_stream

        stream_fn = mock_text_stream
        model = Model(provider="mock", model_id="mock-1")
    else:
        from pi_agent_core.adapters import langchain_stream

        stream_fn = langchain_stream
        model = Model(provider="openai", model_id="gpt-4o-mini")

    agent = Agent(
        initial_state={
            "system_prompt": "You are a helpful assistant.",
            "model": model,
        },
        stream_fn=stream_fn,
    )

    def on_event(event, signal):
        if event.type == "message_update":
            ame = event.assistant_message_event
            if ame.type == "text_delta":
                sys.stdout.write(ame.delta)
                sys.stdout.flush()

    agent.subscribe(on_event)
    await agent.prompt("Say hello in one short sentence.")
    await agent.wait_for_idle()
    print()


if __name__ == "__main__":
    asyncio.run(main())
