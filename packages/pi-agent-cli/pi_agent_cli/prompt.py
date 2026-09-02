"""Deprecated static prompt — use ``build_coding_agent_harness_system_prompt`` instead."""

from __future__ import annotations

from pi_agent_cli.create_harness import build_coding_agent_harness_system_prompt
from pi_agent_cli.system_prompt import BuildSystemPromptOptions
from pi_agent_core.coding_tools import create_coding_tools

__all__ = ["CODING_SYSTEM_PROMPT", "build_coding_agent_harness_system_prompt"]


def _legacy_default_prompt() -> str:
    cwd = "/workspace"
    tools = create_coding_tools(cwd)
    return build_coding_agent_harness_system_prompt(
        cwd=cwd,
        tools=tools,
        active_tool_names=[tool.name for tool in tools],
        system_prompt_options=BuildSystemPromptOptions(cwd=cwd),
    )


CODING_SYSTEM_PROMPT = _legacy_default_prompt()
