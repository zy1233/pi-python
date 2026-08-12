"""Agent with built-in coding tools (read/bash/edit/write/grep/find/ls).

Demonstrates how to create a coding agent that can explore and modify
files in a working directory, using the P6 built-in tool ecosystem.

Usage:
    PI_USE_MOCK=1 python examples/coding_tools_agent.py          # mock (no API)
    PI_API_KEY=sk-... python examples/coding_tools_agent.py      # real API
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from pi_agent_core import Agent, Model
from pi_agent_core.coding_tools import create_coding_tools, create_read_only_tools

USE_MOCK = os.environ.get("PI_USE_MOCK", "0") == "1"


def _create_demo_workspace() -> str:
    """Create a temp directory with sample files for the agent to explore."""
    workspace = tempfile.mkdtemp(prefix="pi-coding-demo-")
    (Path(workspace) / "hello.py").write_text(
        'def greet(name):\n    return f"Hello, {name}!"\n\nprint(greet("World"))\n',
        encoding="utf-8",
    )
    (Path(workspace) / "data.txt").write_text(
        "line 1: alpha\nline 2: beta\nline 3: gamma\nline 4: delta\n",
        encoding="utf-8",
    )
    (Path(workspace) / "README.md").write_text(
        "# Demo Project\n\nA small Python project for testing.\n",
        encoding="utf-8",
    )
    sub = Path(workspace) / "src"
    sub.mkdir()
    (sub / "utils.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    return workspace


def build_agent(workspace: str) -> Agent:
    tools = create_coding_tools(workspace)

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

    return Agent(
        initial_state={
            "system_prompt": (
                f"You are a coding assistant. Your working directory is: {workspace}\n"
                "Use the provided tools to explore and modify files. "
                "Always use the read tool before editing a file."
            ),
            "model": model,
            "tools": tools,
        },
        stream_fn=stream_fn,
        get_api_key=(lambda _: api_key) if api_key else None,
        max_turns=10,
        tool_timeout=30.0,
    )


def build_readonly_agent(workspace: str) -> Agent:
    """Variant with read-only tools (read/grep/find/ls) — cannot modify files."""
    tools = create_read_only_tools(workspace)

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

    return Agent(
        initial_state={
            "system_prompt": (
                f"You are a code reviewer. Working directory: {workspace}\n"
                "You have read-only access. Use grep/find/ls/read to explore."
            ),
            "model": model,
            "tools": tools,
        },
        stream_fn=stream_fn,
        get_api_key=(lambda _: api_key) if api_key else None,
        max_turns=6,
    )


async def main() -> None:
    workspace = _create_demo_workspace()
    print(f"[workspace] {workspace}")
    print(f"[mode] {'mock' if USE_MOCK else 'real API'}\n")

    agent = build_agent(workspace)

    def on_event(event, signal):
        if event.type == "message_update":
            ame = event.assistant_message_event
            if ame.type == "text_delta":
                sys.stdout.write(ame.delta)
                sys.stdout.flush()
        elif event.type == "tool_execution_start":
            print(f"\n  [tool] {event.tool_name}({event.args})")
        elif event.type == "tool_execution_end":
            content = event.result.content if hasattr(event, "result") else ""
            preview = str(content)[:100] if content else ""
            if preview:
                print(f"  [result] {preview}...")

    agent.subscribe(on_event)

    print("--- Task: Explore the project structure ---")
    await agent.prompt(
        "List all files in the project, then read hello.py and summarize what it does."
    )
    await agent.wait_for_idle()
    print("\n")

    if not USE_MOCK:
        print("--- Task: Modify a file ---")
        await agent.prompt(
            "Add a 'subtract' function to src/utils.py, then read the file to confirm."
        )
        await agent.wait_for_idle()
        print("\n")

        result = (Path(workspace) / "src" / "utils.py").read_text(encoding="utf-8")
        print(f"[verification] src/utils.py final content:\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
