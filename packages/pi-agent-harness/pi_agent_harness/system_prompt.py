"""System prompt assembly for AgentHarness resources."""

from __future__ import annotations

from pi_agent_harness.skills import format_skills_for_system_prompt
from pi_agent_harness.types import AgentHarnessResources


def build_harness_system_prompt(base_prompt: str, resources: AgentHarnessResources) -> str:
    """Legacy shim: append skills when the base prompt has no ``<available_skills>`` block."""
    prompt = base_prompt.strip() if base_prompt else "You are a helpful assistant."
    if "<available_skills>" in prompt or not resources.skills:
        return prompt
    skills = format_skills_for_system_prompt(resources.skills)
    if not skills:
        return prompt
    return f"{prompt}{skills}"
