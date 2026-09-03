"""Tests for pi-aligned system prompt assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent_cli.create_harness import build_coding_agent_harness_system_prompt
from pi_agent_cli.system_prompt import BuildSystemPromptOptions, build_system_prompt
from pi_agent_core.coding_tools import create_all_tools, create_coding_tools, create_read_only_tools
from pi_agent_harness.types import Skill

_CWD = "/workspace"


@pytest.mark.parametrize(
    ("factory", "name"),
    [
        (lambda: create_coding_tools(_CWD), "read"),
        (lambda: create_coding_tools(_CWD), "bash"),
        (lambda: create_coding_tools(_CWD), "edit"),
        (lambda: create_coding_tools(_CWD), "write"),
        (lambda: create_read_only_tools(_CWD), "grep"),
        (lambda: create_read_only_tools(_CWD), "find"),
        (lambda: create_read_only_tools(_CWD), "ls"),
    ],
)
def test_tool_contribution_appears_once_in_system_prompt(factory, name):
    tools = factory()
    tool = next(item for item in tools if item.name == name)
    snippet = getattr(tool, "prompt_snippet", None)
    guidelines = list(getattr(tool, "prompt_guidelines", None) or [])

    prompt = build_coding_agent_harness_system_prompt(
        cwd=_CWD,
        tools=tools,
        active_tool_names=[tool.name for tool in tools],
    )

    if snippet:
        assert prompt.count(snippet) == 1
    for guideline in guidelines:
        assert prompt.count(guideline) == 1


def test_bash_pi_guideline_absent_when_session_env_disabled():
    from pi_agent_core.coding_tools.bash import create_bash_tool

    tool = create_bash_tool(_CWD, expose_session_environment=False)
    prompt = build_coding_agent_harness_system_prompt(
        cwd=_CWD,
        tools=[tool],
        active_tool_names=["bash"],
    )
    assert "PI_* environment variables" not in prompt


def test_default_coding_tools_prompt_structure():
    tools = create_coding_tools(_CWD)
    prompt = build_coding_agent_harness_system_prompt(
        cwd=_CWD,
        tools=tools,
        active_tool_names=[tool.name for tool in tools],
    )
    assert "Available tools:" in prompt
    assert "- read: Read file contents" in prompt
    assert "- bash: Execute bash commands" in prompt
    assert "Be concise in your responses" in prompt
    assert f"Current working directory: {_CWD}" in prompt


def test_skills_appended_only_when_read_tool_active():
    skills = [
        Skill(
            name="writer",
            description="Write polished text",
            content="Body",
            filePath="/skills/writer/SKILL.md",
        )
    ]
    read_only = [tool for tool in create_read_only_tools(_CWD) if tool.name != "read"]
    prompt_without_read = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=_CWD,
            selected_tools=[tool.name for tool in read_only],
            tool_snippets={
                tool.name: tool.prompt_snippet
                for tool in read_only
                if getattr(tool, "prompt_snippet", None)
            },
            skills=skills,
        )
    )
    assert "<available_skills>" not in prompt_without_read

    coding_tools = create_coding_tools(_CWD)
    prompt_with_read = build_coding_agent_harness_system_prompt(
        cwd=_CWD,
        tools=coding_tools,
        active_tool_names=[tool.name for tool in coding_tools],
        system_prompt_options=BuildSystemPromptOptions(cwd=_CWD, skills=skills),
    )
    assert "<available_skills>" in prompt_with_read
    assert "<name>writer</name>" in prompt_with_read


def test_project_context_in_custom_prompt():
    from pi_agent_cli.system_prompt import ContextFile

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=_CWD,
            custom_prompt="Custom body",
            context_files=[ContextFile(path="AGENTS.md", content="Be careful")],
        )
    )
    assert prompt.startswith("Custom body")
    assert "<project_context>" in prompt
    assert '<project_instructions path="AGENTS.md">' in prompt
    assert "Be careful" in prompt


def test_golden_default_four_tool_snapshot():
    from pi_agent_cli.system_prompt import _docs_paths

    tools = create_coding_tools(_CWD)
    prompt = build_coding_agent_harness_system_prompt(
        cwd=_CWD,
        tools=tools,
        active_tool_names=[tool.name for tool in tools],
    )
    readme_path, docs_path, _ = _docs_paths()
    normalized_prompt = (
        prompt.replace(readme_path, "<PI_DOCS>/README.md")
        .replace(docs_path, "<PI_DOCS>/docs")
        .replace("\r\n", "\n")
    )
    golden = Path(__file__).with_name("snapshots") / "default_coding_tools_prompt.txt"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(normalized_prompt, encoding="utf-8")
        pytest.skip("golden snapshot created")
    expected = golden.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert normalized_prompt == expected


def test_all_seven_tools_listed():
    tools = list(create_all_tools(_CWD).values())
    prompt = build_coding_agent_harness_system_prompt(
        cwd=_CWD,
        tools=tools,
        active_tool_names=[tool.name for tool in tools],
    )
    for name in ("read", "bash", "edit", "write", "grep", "find", "ls"):
        assert f"- {name}:" in prompt
