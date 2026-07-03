"""System prompt assembly for AgentHarness resources."""

from __future__ import annotations

from pi_agent_harness.skills import format_skills_for_system_prompt
from pi_agent_harness.types import AgentHarnessResources


def build_harness_system_prompt(base_prompt: str, resources: AgentHarnessResources) -> str:
    parts = [base_prompt.strip() if base_prompt else "You are a helpful assistant."]
    if resources.skills:
        skills = format_skills_for_system_prompt(resources.skills)
        if skills:
            parts.extend(
                [
                    "",
                    "Available skills. Use them when relevant; call harness.skill(name) only when "
                    "the user explicitly asks to run a skill.",
                    skills,
                ]
            )
    return "\n".join(parts)
