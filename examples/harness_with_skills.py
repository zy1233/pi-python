"""AgentHarness with skills, prompt templates, and hooks.

Demonstrates advanced harness features: skill registration, prompt templates
with argument substitution, tool call hooks for logging/blocking, and
the before_agent_start hook for context injection.

Usage:
    PI_USE_MOCK=1 python examples/harness_with_skills.py       # mock (no API)
    PI_API_KEY=sk-... python examples/harness_with_skills.py   # real API
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from pi_agent_core import Model
from pi_agent_core.coding_tools import create_read_only_tools
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult

USE_MOCK = os.environ.get("PI_USE_MOCK", "0") == "1"


# --- Custom tool: search documentation ---


class SearchDocsParams(BaseModel):
    query: str = Field(description="Search query for documentation")


async def search_docs(_id, params: SearchDocsParams, signal, on_update):
    """Simulated documentation search."""
    results = {
        "flask": "Flask is a micro web framework for Python. Key features: routing, "
        "templates, request/response objects, sessions.",
        "route": "@app.route(rule) - Decorator to bind a URL rule to a view function.",
        "json": "flask.jsonify(*args, **kwargs) - Creates a Response with JSON data.",
    }
    matches = [v for k, v in results.items() if k in params.query.lower()]
    text = "\n".join(matches) if matches else f"No docs found for: {params.query}"
    return AgentToolResult(content=[{"type": "text", "text": text}], details={})


def _create_workspace() -> str:
    workspace = tempfile.mkdtemp(prefix="pi-skills-demo-")
    (Path(workspace) / "app.py").write_text(
        "from flask import Flask, jsonify\n\napp = Flask(__name__)\n\n"
        '@app.route("/")\ndef index():\n    return jsonify(message="Hello!")\n',
        encoding="utf-8",
    )
    # Create a skill file
    skills_dir = Path(workspace) / ".skills"
    skills_dir.mkdir()
    (skills_dir / "review.md").write_text(
        "---\n"
        "name: review\n"
        "description: Review Python code for best practices\n"
        "---\n\n"
        "Review the code in the workspace for:\n"
        "1. Missing type hints\n"
        "2. Missing docstrings\n"
        "3. Security issues\n"
        "4. Performance concerns\n\n"
        "Provide a brief summary of findings.\n",
        encoding="utf-8",
    )
    (skills_dir / "explain.md").write_text(
        "---\n"
        "name: explain\n"
        "description: Explain code structure and architecture\n"
        "---\n\n"
        "Explain the overall architecture of the project:\n"
        "- Entry points\n"
        "- Key modules and their responsibilities\n"
        "- Data flow\n",
        encoding="utf-8",
    )
    return workspace


async def main() -> None:
    from pi_agent_harness import (
        AgentHarness,
        AgentHarnessResources,
        LocalExecutionEnv,
        MemorySessionRepo,
        PromptTemplate,
        load_skills,
    )

    workspace = _create_workspace()
    env = LocalExecutionEnv(workspace)
    print(f"[workspace] {workspace}")
    print(f"[mode] {'mock' if USE_MOCK else 'real API'}\n")

    # --- Load skills from workspace ---
    skill_result = await load_skills(env, [".skills"])
    print(f"[skills loaded] {[s.name for s in skill_result.skills]}")
    if skill_result.diagnostics:
        for d in skill_result.diagnostics:
            print(f"  [warn] {d.message}")

    # --- Prompt templates (use $1, $2 for positional args, $@ for all) ---
    templates = [
        PromptTemplate(
            name="quick-review",
            description="Quick code review of a specific file",
            content="Review the file $1 for issues. Be concise.",
        ),
        PromptTemplate(
            name="add-tests",
            description="Generate unit tests for a module",
            content="Write pytest unit tests for $1. Cover edge cases.",
        ),
    ]

    # --- Model & stream ---
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

    # --- Build tools ---
    read_tools = create_read_only_tools(workspace)
    custom_tool = SimpleTool(
        name="search_docs",
        description="Search framework documentation",
        label="Docs",
        parameters=SearchDocsParams,
        execute_fn=search_docs,
    )

    # --- Harness with resources ---
    repo = MemorySessionRepo()
    session = await repo.create()

    harness = AgentHarness(
        session=session,
        model=model,
        stream_fn=stream_fn,
        env=env,
        tools=[*read_tools, custom_tool],
        get_api_key=(lambda _: api_key) if api_key else None,
        resources=AgentHarnessResources(
            skills=skill_result.skills,
            promptTemplates=templates,
        ),
        system_prompt=(
            f"You are a senior developer assistant. Working directory: {workspace}\n"
            "You have read-only file access and a documentation search tool.\n"
            "When reviewing code, be specific about line numbers and issues."
        ),
        max_turns=6,
        tool_timeout=30.0,
    )

    # --- Hook: log all tool calls ---
    tool_log: list[dict] = []

    def on_tool_call(event):
        tool_log.append({"tool": event.toolName, "input": event.input})
        print(f"  [hook:tool_call] {event.toolName}({event.input})")

    harness.on("tool_call", on_tool_call)

    # --- Event listener ---
    def on_event(event, signal):
        etype = getattr(event, "type", "")
        if etype == "message_update":
            ame = event.assistant_message_event
            if ame.type == "text_delta":
                sys.stdout.write(ame.delta)
                sys.stdout.flush()
        elif etype == "tool_execution_start":
            print(f"\n  [exec] {event.tool_name}")

    harness.subscribe(on_event)

    # --- Demo 1: Use a skill ---
    print("=== Invoke 'explain' skill ===")
    await harness.prompt(
        "Use skill `explain`.\nReferences are relative to .skills.\n\n"
        "Explain the overall architecture of the project:\n"
        "- Entry points\n- Key modules and their responsibilities\n- Data flow"
    )
    print("\n")

    if not USE_MOCK:
        # --- Demo 2: Use prompt template ---
        from pi_agent_harness import substitute_args

        template = templates[0]  # quick-review
        prompt_text = substitute_args(template.template, ["app.py"])
        print(f"=== Prompt template: {template.name} -> '{prompt_text}' ===")
        await harness.prompt(prompt_text)
        print("\n")

        # --- Demo 3: Free-form with docs tool ---
        print("=== Free-form: search docs + analysis ===")
        await harness.prompt(
            "Search the docs for 'flask route' and 'json', then suggest how to "
            "improve the existing /index endpoint in app.py."
        )
        print("\n")

    # --- Summary ---
    print(f"\n[tool call log] {len(tool_log)} calls made:")
    for entry in tool_log:
        print(f"  - {entry['tool']}: {entry['input']}")

    await env.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
