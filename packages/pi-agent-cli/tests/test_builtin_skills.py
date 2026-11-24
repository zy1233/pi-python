"""Tests for built-in skills (`skill-creator`), config toggling, and precedence."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest

from pi_agent_cli.builtin_skills import builtin_skills_dir, load_builtin_skills
from pi_agent_cli.config import CliConfig, load_config
from pi_agent_cli.extension_trust import decide_project_trust, skipped_project_resources
from pi_agent_cli.factory import create_session_harness, load_session_resources
from pi_agent_cli.trust_fingerprint import gated_project_resources
from pi_agent_core.coding_tools.read import ReadParams, create_read_tool
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import JsonlSessionRepo, LocalExecutionEnv
from pi_agent_harness.skills import SKILL_NAME_RE, format_skills_for_system_prompt


@pytest.mark.asyncio
async def test_load_builtin_skills_loads_valid_skill_creator(tmp_path: Path):
    env = LocalExecutionEnv(tmp_path)
    result = await load_builtin_skills(env)

    assert result.diagnostics == []
    assert [skill.name for skill in result.skills] == ["skill-creator"]

    skill = result.skills[0]
    assert SKILL_NAME_RE.match(skill.name)
    assert len(skill.name) <= 64
    assert 1 <= len(skill.description) <= 1024
    assert skill.disableModelInvocation is False
    assert "\\" not in skill.filePath
    assert skill.filePath.endswith("/skill-creator/SKILL.md")
    assert Path(skill.filePath).is_file()


@pytest.mark.asyncio
async def test_skill_creator_references_exist_and_are_readable(tmp_path: Path):
    result = await load_builtin_skills(LocalExecutionEnv(tmp_path))
    skill = result.skills[0]
    skill_dir = Path(skill.filePath).parent

    refs = sorted(set(re.findall(r"references/[a-z0-9._-]+\.md", skill.content)))
    assert refs == [
        "references/authoring-guide.md",
        "references/installing-skills.md",
    ]

    read_tool = create_read_tool(str(tmp_path))
    skill_read = await read_tool.execute("read-skill", ReadParams(path=skill.filePath))
    assert "Skill Creator" in skill_read.content[0]["text"]

    for rel in refs:
        target = skill_dir / rel
        assert target.is_file()
        posix_target = str(PurePosixPath(skill.filePath).parent / rel)
        ref_read = await read_tool.execute(f"read-{rel}", ReadParams(path=posix_target))
        assert ref_read.content[0]["text"].strip()


@pytest.mark.asyncio
async def test_load_session_resources_includes_builtin_skills_by_default(tmp_path: Path):
    resources = await load_session_resources(cwd=tmp_path, config=CliConfig(), trusted=False)
    skills = resources.skills or []

    assert [skill.name for skill in skills] == ["skill-creator"]


def test_config_include_builtin_parsing_and_validation(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        "[skills]\ninclude_builtin = false\n",
        encoding="utf-8",
    )
    disabled = load_config(tmp_path)
    assert disabled.include_builtin_skills is False
    assert disabled.auto.notes == ()

    (tmp_path / "agent.toml").write_text(
        '[skills]\ninclude_builtin = "yes"\n',
        encoding="utf-8",
    )
    invalid = load_config(tmp_path)
    assert invalid.include_builtin_skills is True
    assert any("include_builtin in [skills]" in note for note in invalid.auto.notes)


@pytest.mark.asyncio
async def test_load_session_resources_respects_include_builtin_false(tmp_path: Path):
    config = CliConfig(include_builtin_skills=False)
    resources = await load_session_resources(cwd=tmp_path, config=config, trusted=True)

    assert resources.skills is None


@pytest.mark.asyncio
async def test_configured_skills_override_builtin_skill_by_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    user_skills = home / "skills"
    (user_skills / "skill-creator").mkdir(parents=True)
    (user_skills / "skill-creator" / "SKILL.md").write_text(
        "---\nname: skill-creator\ndescription: User override\n---\nUser body\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    project = tmp_path / "project"
    (project / ".pi" / "skills" / "skill-creator").mkdir(parents=True)
    (project / ".pi" / "skills" / "skill-creator" / "SKILL.md").write_text(
        "---\nname: skill-creator\ndescription: Project override\n---\nProject body\n",
        encoding="utf-8",
    )

    user_only = await load_session_resources(
        cwd=project,
        config=CliConfig(skills_dirs=("~/skills", ".pi/skills")),
        trusted=False,
    )
    assert [s.name for s in user_only.skills or []] == ["skill-creator"]
    assert user_only.skills[0].description == "User override"
    assert user_only.skills[0].content == "User body"
    assert str(builtin_skills_dir()) not in user_only.skills[0].filePath

    project_wins = await load_session_resources(
        cwd=project,
        config=CliConfig(skills_dirs=("~/skills", ".pi/skills")),
        trusted=True,
    )
    assert [s.name for s in project_wins.skills or []] == ["skill-creator"]
    assert project_wins.skills[0].description == "Project override"
    assert project_wins.skills[0].content == "Project body"


@pytest.mark.asyncio
async def test_user_override_with_disable_model_invocation_hides_builtin(tmp_path: Path):
    user_skills = tmp_path / "user-skills"
    (user_skills / "skill-creator").mkdir(parents=True)
    (user_skills / "skill-creator" / "SKILL.md").write_text(
        "---\nname: skill-creator\ndescription: Suppressed\n"
        "disable-model-invocation: true\n---\nHidden\n",
        encoding="utf-8",
    )

    resources = await load_session_resources(
        cwd=tmp_path,
        config=CliConfig(skills_dirs=(str(user_skills),)),
        trusted=False,
    )
    assert [s.name for s in resources.skills or []] == ["skill-creator"]
    assert resources.skills[0].disableModelInvocation is True
    assert format_skills_for_system_prompt(resources.skills or []) == ""


def test_builtin_skills_do_not_trigger_project_trust(tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    config = CliConfig()
    decision = decide_project_trust(config, project, home=home)

    assert decision.reason == "nothing"
    assert gated_project_resources(config, project, home=home) == []
    assert skipped_project_resources(config, project, home=home) == []


@pytest.mark.asyncio
async def test_default_session_harness_sends_skill_creator_in_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    seen: list[str] = []

    async def capturing_stream(model, context, options=None):
        seen.append(context.system_prompt)
        return await mock_text_stream(model, context, options)

    config = CliConfig()
    repo = JsonlSessionRepo(tmp_path / "sessions")
    session = await repo.create({"cwd": str(tmp_path)})
    resources = await load_session_resources(cwd=tmp_path, config=config)
    harness = await create_session_harness(
        session=session,
        cwd=tmp_path,
        config=config,
        stream_fn=capturing_stream,
        resources=resources,
        home=tmp_path,
    )

    await harness.prompt("hello")

    assert seen
    assert "<available_skills>" in seen[-1]
    assert "<name>skill-creator</name>" in seen[-1]
