"""H4 tests for resources, prompt templates, system prompt, and LocalExecutionEnv."""

from __future__ import annotations

import asyncio
import contextlib
import sys

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
    load_sourced_skills,
)
from pi_agent_harness.types import (
    AgentHarnessResources,
    ExecutionError,
    FileError,
    PromptTemplate,
    Skill,
)

PYTHON = sys.executable


def _model() -> Model:
    return Model(provider="mock", model_id="m1")


def _user_text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if block.get("type") == "text")
    return str(content)


async def _session() -> Session:
    return Session(await MemorySessionStorage.create(session_id="h4"))


class _AbortSignal:
    """Minimal abort signal compatible with LocalExecutionEnv.exec."""

    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    async def wait_aborted(self) -> None:
        await self._event.wait()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()


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
    user_text = _user_text(user_messages[-1])
    assert "Use concise prose." in user_text
    assert "Answer in Chinese." in user_text


@pytest.mark.asyncio
async def test_prompt_templates_load_substitute_and_prompt():
    seen: list[str] = []

    async def recording_stream(model, context, options=None):
        seen.extend(_user_text(m) for m in context.messages if getattr(m, "role", None) == "user")
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


# ---------------------------------------------------------------------------
# Skills: ignore multi-level inheritance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_skills_inherits_ignore_rules_to_child_directories(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file(
        "skills/visible/SKILL.md",
        "---\nname: visible\ndescription: Visible skill\n---\nContent.",
    )
    await env.write_file(
        "skills/sub/secret/SKILL.md",
        "---\nname: secret\ndescription: Secret skill\n---\nHidden.",
    )
    await env.write_file(
        "skills/sub/ok/SKILL.md",
        "---\nname: ok\ndescription: Ok skill\n---\nOk content.",
    )
    await env.write_file("skills/sub/.ignore", "secret/\n")

    result = await load_skills(env, ["skills"])
    names = sorted(s.name for s in result.skills)

    assert "visible" in names
    assert "ok" in names
    assert "secret" not in names


# ---------------------------------------------------------------------------
# Skills: SKILL.md stops recursion into subdirectories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_skills_stops_recursion_at_skill_md(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file(
        "skills/parent/SKILL.md",
        "---\nname: parent\ndescription: Parent skill\n---\nParent content.",
    )
    await env.write_file(
        "skills/parent/child/SKILL.md",
        "---\nname: child\ndescription: Child skill\n---\nChild content.",
    )

    result = await load_skills(env, ["skills"])
    names = [s.name for s in result.skills]

    assert names == ["parent"]


# ---------------------------------------------------------------------------
# Skills: YAML parse failure produces diagnostic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_skills_yaml_parse_failure_produces_diagnostic(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file(
        "skills/broken/SKILL.md",
        "---\n: [invalid yaml\n---\nBody.",
    )

    result = await load_skills(env, ["skills"])

    assert result.skills == []
    assert len(result.diagnostics) >= 1
    assert result.diagnostics[0].code in ("read_failed", "parse_failed")


# ---------------------------------------------------------------------------
# Skills: load_sourced_skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_sourced_skills_groups_by_source(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file(
        "a/writer/SKILL.md",
        "---\nname: writer\ndescription: Write text\n---\nWrite.",
    )
    await env.write_file(
        "b/reader/SKILL.md",
        "---\nname: reader\ndescription: Read text\n---\nRead.",
    )

    results = await load_sourced_skills(env, {"src-a": ["a"], "src-b": ["b"]})

    assert [s.name for s in results["src-a"].skills] == ["writer"]
    assert [s.name for s in results["src-b"].skills] == ["reader"]


# ---------------------------------------------------------------------------
# Prompt templates: ${@:N} and ${@:N:L} range slice substitution
# ---------------------------------------------------------------------------


def test_substitute_args_range_slice_syntax():
    args = ["a", "b", "c", "d", "e"]

    assert substitute_args("${@:2}", args) == "b c d e"
    assert substitute_args("${@:3:2}", args) == "c d"
    assert substitute_args("prefix ${@:1} suffix", args) == "prefix a b c d e suffix"
    assert substitute_args("${@:4:1}", args) == "d"
    assert substitute_args("${@:10}", args) == ""


# ---------------------------------------------------------------------------
# System prompt: callback receives correct parameters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_callback_receives_correct_params():
    received = {}

    async def custom_system_prompt(params):
        received.update(params)
        return "Custom system prompt"

    skill = Skill(
        name="test-skill",
        description="Test",
        content="Content.",
        filePath="/skills/test-skill/SKILL.md",
    )
    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=mock_text_stream,
        system_prompt=custom_system_prompt,
        resources=AgentHarnessResources(skills=[skill]),
        thinking_level="off",
    )

    await harness.prompt("hello")

    assert "env" in received
    assert "session" in received
    assert "model" in received
    assert received["model"].provider == "mock"
    assert "thinking_level" in received
    assert "active_tools" in received
    assert "resources" in received
    assert received["resources"].skills is not None


# ---------------------------------------------------------------------------
# System prompt: no skills → no <skills> block injected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_no_skills_omits_skills_block():
    seen: dict[str, object] = {}

    async def recording_stream(model, context, options=None):
        seen["system_prompt"] = context.system_prompt
        return await mock_text_stream(model, context, options)

    harness = AgentHarness(
        session=await _session(),
        model=_model(),
        stream_fn=recording_stream,
    )
    await harness.prompt("hello")

    sp = str(seen["system_prompt"])
    assert "<skills>" not in sp


# ---------------------------------------------------------------------------
# LocalExecutionEnv: read_binary_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_read_binary_file(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    data = b"\x00\x01\x02\xff"
    await env.write_file("bin.dat", data)

    result = await env.read_binary_file("bin.dat")

    assert result == data


# ---------------------------------------------------------------------------
# LocalExecutionEnv: create_dir / remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_create_dir_and_remove(tmp_path):
    env = LocalExecutionEnv(tmp_path)

    await env.create_dir("a/b/c")
    assert await env.exists("a/b/c")

    await env.write_file("a/b/c/file.txt", "content")
    await env.remove("a/b")
    assert not await env.exists("a/b")
    assert await env.exists("a")

    await env.remove("nonexistent")


# ---------------------------------------------------------------------------
# LocalExecutionEnv: create_temp_dir / create_temp_file / cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_temp_lifecycle(tmp_path):
    env = LocalExecutionEnv(tmp_path)

    td = await env.create_temp_dir()
    tf = await env.create_temp_file(suffix=".txt")
    from pathlib import Path

    assert Path(td).is_dir()
    assert Path(tf).is_file()

    await env.cleanup()

    assert not Path(td).exists()
    assert not Path(tf).exists()
    assert env._temp_paths == []


# ---------------------------------------------------------------------------
# LocalExecutionEnv: canonical_path / absolute_path / exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_path_resolution(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    await env.write_file("x.txt", "hi")

    abs_path = await env.absolute_path("x.txt")
    canon_path = await env.canonical_path("x.txt")

    assert "x.txt" in abs_path
    assert "x.txt" in canon_path
    assert await env.exists("x.txt")
    assert not await env.exists("nonexistent.txt")


# ---------------------------------------------------------------------------
# LocalExecutionEnv: file_info raises FileError for missing path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_file_info_not_found(tmp_path):
    env = LocalExecutionEnv(tmp_path)

    with pytest.raises(FileError) as exc_info:
        await env.file_info("no_such_file.txt")
    assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# LocalExecutionEnv: exec timeout kills process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_exec_timeout():
    env = LocalExecutionEnv(".")
    with pytest.raises(ExecutionError) as exc_info:
        await env.exec(f'{PYTHON} -c "import time; time.sleep(30)"', timeout=0.3)
    assert exc_info.value.code == "timeout"


# ---------------------------------------------------------------------------
# LocalExecutionEnv: exec signal abort before start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_exec_signal_abort_before_start():
    env = LocalExecutionEnv(".")
    signal = _AbortSignal()
    signal.abort()

    with pytest.raises(ExecutionError) as exc_info:
        await env.exec(f'{PYTHON} -c "print(1)"', signal=signal)
    assert exc_info.value.code == "aborted"


# ---------------------------------------------------------------------------
# LocalExecutionEnv: exec signal abort mid-execution (H4-1 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_execution_env_exec_signal_abort_mid_execution():
    env = LocalExecutionEnv(".")
    signal = _AbortSignal()

    async def abort_after_delay():
        await asyncio.sleep(0.3)
        signal.abort()

    abort_task = asyncio.create_task(abort_after_delay())
    with pytest.raises(ExecutionError) as exc_info:
        await env.exec(f'{PYTHON} -c "import time; time.sleep(30)"', signal=signal)
    assert exc_info.value.code == "aborted"
    abort_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await abort_task
