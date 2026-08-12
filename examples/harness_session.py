"""AgentHarness with session persistence and multi-turn conversation.

Demonstrates the full harness runtime: session tree storage, automatic
context replay, tool call hooks, compaction settings, and event streaming.

Usage:
    PI_USE_MOCK=1 python examples/harness_session.py          # mock (no API)
    PI_API_KEY=sk-... python examples/harness_session.py      # real API
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from pi_agent_core import Model
from pi_agent_core.coding_tools import create_coding_tools

USE_MOCK = os.environ.get("PI_USE_MOCK", "0") == "1"


def _create_workspace() -> str:
    workspace = tempfile.mkdtemp(prefix="pi-harness-demo-")
    (Path(workspace) / "app.py").write_text(
        "from flask import Flask\n\napp = Flask(__name__)\n\n"
        '@app.route("/")\ndef index():\n    return "Hello!"\n',
        encoding="utf-8",
    )
    return workspace


async def main() -> None:
    from pi_agent_harness import (
        AgentHarness,
        LocalExecutionEnv,
        MemorySessionRepo,
    )

    workspace = _create_workspace()
    print(f"[workspace] {workspace}")
    print(f"[mode] {'mock' if USE_MOCK else 'real API'}\n")

    # --- Model & stream setup ---
    if USE_MOCK:
        from pi_agent_core.tests.mock_stream import mock_tool_stream

        stream_fn = mock_tool_stream
        model = Model(provider="mock", model_id="mock-1")
        api_key = None
    else:
        from pi_agent_core.adapters import langchain_stream

        stream_fn = langchain_stream
        model = Model(
            provider=os.environ.get("PI_PROVIDER", "deepseek"),
            model_id=os.environ.get("PI_MODEL", "deepseek-ai/DeepSeek-V4-Flash"),
            base_url=os.environ.get("PI_BASE_URL", "https://api.siliconflow.cn/v1"),
            context_window=32_000,
        )
        api_key = os.environ.get("PI_API_KEY")

    # --- Session setup (in-memory for this demo) ---
    repo = MemorySessionRepo()
    session = await repo.create()

    # --- Execution environment ---
    env = LocalExecutionEnv(workspace)

    # --- Coding tools bound to workspace ---
    tools = create_coding_tools(workspace)

    # --- Build harness ---
    harness = AgentHarness(
        session=session,
        model=model,
        stream_fn=stream_fn,
        env=env,
        tools=tools,
        get_api_key=(lambda _: api_key) if api_key else None,
        system_prompt=(
            f"You are a coding assistant. Working directory: {workspace}\n"
            "Use tools to read and modify code. Be concise in explanations."
        ),
        max_turns=8,
        tool_timeout=30.0,
        compaction={"threshold": 0.8, "strategy": "trim_oldest"},
    )

    # --- Event listener ---
    def on_event(event, signal):
        etype = getattr(event, "type", "")
        if etype == "message_update":
            ame = event.assistant_message_event
            if ame.type == "text_delta":
                sys.stdout.write(ame.delta)
                sys.stdout.flush()
        elif etype == "tool_execution_start":
            print(f"\n  [tool] {event.tool_name}({event.args})")
        elif etype == "save_point":
            print("  [session saved]")
        elif etype == "settled":
            print(f"  [settled] next_turn_queue={event.nextTurnCount}")

    harness.subscribe(on_event)

    # --- Multi-turn conversation (session accumulates context) ---
    print("=== Turn 1: Read the project ===")
    await harness.prompt("Read app.py and describe what it does.")
    print("\n")

    if not USE_MOCK:
        print("=== Turn 2: Add a feature ===")
        await harness.prompt(
            "Add a /health endpoint that returns JSON {'status': 'ok'}. Edit app.py directly."
        )
        print("\n")

        print("=== Turn 3: Verify (agent remembers context from turn 1 & 2) ===")
        await harness.prompt("Read app.py again and confirm the health endpoint is there.")
        print("\n")

        result = (Path(workspace) / "app.py").read_text(encoding="utf-8")
        print(f"[final app.py]\n{result}")

    # --- Session metadata ---
    metadata = await session.get_metadata()
    print(f"\n[session] id={metadata.id} created={metadata.createdAt}")

    await env.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
