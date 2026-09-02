"""Coding-agent harness helpers (port of pi ``create-harness.ts``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pi_agent_cli.system_prompt import (
    BuildSystemPromptOptions,
    build_system_prompt,
    normalize_tool_snippet,
)
from pi_agent_core.types import AgentTool


def _tool_prompt_snippet(tool: Any) -> str | None:
    snippet = getattr(tool, "prompt_snippet", None)
    if not snippet:
        return None
    normalized = normalize_tool_snippet(str(snippet))
    return normalized or None


def _tool_prompt_guidelines(tool: Any) -> list[str]:
    guidelines = getattr(tool, "prompt_guidelines", None)
    if not guidelines:
        return []
    return list(guidelines)


def build_coding_agent_harness_system_prompt(
    *,
    cwd: str,
    tools: Sequence[AgentTool],
    active_tool_names: Sequence[str],
    system_prompt_options: BuildSystemPromptOptions | None = None,
) -> str:
    """Collect tool contributions and build the coding-agent system prompt."""
    tool_by_name = {tool.name: tool for tool in tools}
    active_tools = [tool_by_name[name] for name in active_tool_names if name in tool_by_name]

    tool_snippets: dict[str, str] = {}
    for tool in active_tools:
        snippet = _tool_prompt_snippet(tool)
        if snippet:
            tool_snippets[tool.name] = snippet

    prompt_guidelines: list[str] = []
    for tool in active_tools:
        prompt_guidelines.extend(_tool_prompt_guidelines(tool))

    base = system_prompt_options or BuildSystemPromptOptions(cwd=cwd)
    return build_system_prompt(
        BuildSystemPromptOptions(
            cwd=cwd,
            custom_prompt=base.custom_prompt,
            selected_tools=[tool.name for tool in active_tools],
            tool_snippets=tool_snippets,
            prompt_guidelines=prompt_guidelines,
            append_system_prompt=base.append_system_prompt,
            context_files=base.context_files,
            skills=base.skills,
        )
    )
