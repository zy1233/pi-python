"""H4 tests for resources, prompt templates, system prompt, and LocalExecutionEnv."""

from __future__ import annotations

import pytest

from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import Model
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session
from pi_agent_harness.env import LocalExecutionEnv
from pi_agent_harness.prompt_templates import (
    load_prompt_templates,
    parse_command_args,
    substitute_args,
)
from pi_agent_harness.skills import (
    format_skill_invocation,
    format_skills_for_system_prompt,
    load_skills,
)
from pi_agent_harness.types import AgentHarnessResources, PromptTemplate, Skill


def _model() -> Model:
    return Model(provider="mock", model_id="m1")


async def _session() -> Session:
    return Session(await MemorySessionStorage.create(cwd="/workspace", session_id="h4"))


@pytest.mark.asyncio
async def test_local_execution_env_file_operations_and_shell(tmp_path):
    env = LocalExecutionEnv(tmp_path)

    await env.write_file("notes/a.txt", "hello")
    await env.append_file("notes/a.txt", "\nworld")
    assert await env.read_text_file("notes/a.txt") == "hello\nworld"
    assert await env.read_text_lines("notes/a.txt", max_lines=1) == ["hello"]

    info = await env.file_info("notes")
    assert info.kind == "directory"
    listed = await env.list_dir("notes")
    assert [entry.name for entry in listed] == ["a.txt"]

    result = await env.exec("python -c \"print('ok')\"", timeout=5)
    assert result.exitCode == 0
    assert result.stdout.strip() == "ok"


@pytest.mark.asyncio
async def test_load_skills_parses_frontmatter_and_ignore_rules(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file(
        "skills/writer/SKILL.md",
        "---\nname: writer\ndescription: Write polished text\n---\nUse concise prose.",
    )
    await env.write_file(
        "skills/hidden/SKILL.md",
        "---\nname: hidden\ndescription: Hidden skill\n---\nShould be ignored.",
    )
    await env.write_file(
        "skills/bad/SKILL.md",
        "---\nname: bad\ndescription: ''\n---\nMissing description.",
    )
    await env.write_file("skills/.gitignore", "hidden/\n")

    result = await load_skills(env, ["skills"])

    assert [skill.name for skill in result.skills] == ["writer"]
    assert result.skills[0].description == "Write polished text"
    assert result.diagnostics[0].code == "invalid_metadata"


def test_format_skills_for_system_prompt_and_invocation():
    skills = [
        Skill(
            name="writer",
            description="Write polished text",
            content="Use concise prose.",
            filePath="/skills/writer/SKILL.md",
        ),
        Skill(
            name="hidden",
            description="Hidden",
            content="secret",
            filePath="/skills/hidden/SKILL.md",
            disableModelInvocation=True,
        ),
    ]

    system_prompt = format_skills_for_system_prompt(skills)
    invocation = format_skill_invocation(skills[0], "Use Chinese.")

    assert '<skill name="writer"' in system_prompt
    assert "hidden" not in system_prompt
    assert "Use concise prose." in invocation
    assert "Use Chinese." in invocation


@pytest.mark.asyncio
async def test_agent_harness_injects_skills_and_runs_skill_prompt():
    seen: dict[str, object] = {}

    async def recording_stream(model, context, options=None):
        seen["system_prompt"] = context.system_prompt
        seen["messages"] = context.messages
        return await mock_text_stream(model, context, options)

    skill = Skill(
        name="writer",
        description="Write polished text",
        content="Use concise prose.",
        filePath="/skills/writer/SKILL.md",
    )
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=recording_stream,
        resources=AgentHarnessResources(skills=[skill]),
    )

    await harness.skill("writer", "Answer in Chinese.")

    assert "writer" in str(seen["system_prompt"])
    user_messages = [m for m in seen["messages"] if getattr(m, "role", None) == "user"]
    assert "Use concise prose." in user_messages[-1].content
    assert "Answer in Chinese." in user_messages[-1].content


@pytest.mark.asyncio
async def test_prompt_templates_load_substitute_and_prompt():
    seen: list[str] = []

    async def recording_stream(model, context, options=None):
        seen.extend(m.content for m in context.messages if getattr(m, "role", None) == "user")
        return await mock_text_stream(model, context, options)

    assert parse_command_args("'hello world' test") == ["hello world", "test"]
    assert substitute_args("Say $1 to $@ / $ARGUMENTS", ["hello world", "again"]) == (
        "Say hello world to hello world again / hello world again"
    )

    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=recording_stream,
        resources=AgentHarnessResources(
            promptTemplates=[
                PromptTemplate(name="greet", description="Greet", content="Say $1 to $2")
            ]
        ),
    )
    await harness.prompt_from_template("greet", ["hello", "world"])

    assert seen[-1] == "Say hello to world"


@pytest.mark.asyncio
async def test_load_prompt_templates_from_directory(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file("prompts/greet.md", "---\ndescription: Greet user\n---\nHi $1")

    templates = await load_prompt_templates(env, ["prompts"])

    assert templates[0].name == "greet"
    assert templates[0].description == "Greet user"
    assert templates[0].content == "Hi $1"
