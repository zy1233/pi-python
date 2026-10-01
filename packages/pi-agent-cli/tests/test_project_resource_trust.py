"""Project prompt files and skills sit behind the same project trust as extensions.

Upstream pi puts ``.pi/SYSTEM.md``, ``.pi/APPEND_SYSTEM.md`` and project skills behind project
trust next to extensions. They do not run code, but they decide what the model is told, so a
cloned repository must not get to rewrite the system prompt just by being opened.
``AGENTS.md`` / ``CLAUDE.md`` stay ungated, as upstream loads them whatever the trust.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig, is_project_relative_path
from pi_agent_cli.extension_trust import (
    TRUST_ENV,
    ProjectTrust,
    skipped_project_resources,
    untrusted_project_notice,
)
from pi_agent_cli.factory import create_session_harness, load_session_resources
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import JsonlSessionRepo

SKILL = "---\nname: {name}\ndescription: {name} skill\n---\nBody\n"


@pytest.fixture(autouse=True)
def _clean_trust_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A project shipping its own prompt files, a skill and an AGENTS.md, and an empty home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])

    project = tmp_path / "project"
    (project / ".pi" / "skills" / "shipped").mkdir(parents=True)
    (project / ".pi" / "SYSTEM.md").write_text("Project system", encoding="utf-8")
    (project / ".pi" / "APPEND_SYSTEM.md").write_text("Project append", encoding="utf-8")
    (project / ".pi" / "skills" / "shipped" / "SKILL.md").write_text(
        SKILL.format(name="shipped"), encoding="utf-8"
    )
    (project / "AGENTS.md").write_text("Project agents rules", encoding="utf-8")
    return SimpleNamespace(tmp=tmp_path, home=home, pi_home=home / ".pi-python", project=project)


def _trusting(world: Any, **kwargs: Any) -> CliConfig:
    return CliConfig(trusted_projects=(str(world.project),), **kwargs)


def _options(world: Any, config: CliConfig):
    return load_system_prompt_options(cwd=world.project, config=config, home=world.pi_home)


def _skill_names(resources: Any) -> list[str]:
    return [skill.name for skill in (resources.skills or [])]


# ---------------------------------------------------------------------------
# .pi/SYSTEM.md and .pi/APPEND_SYSTEM.md
# ---------------------------------------------------------------------------


def test_an_untrusted_project_cannot_replace_or_extend_the_system_prompt(world: Any):
    options = _options(world, CliConfig())

    assert options.custom_prompt is None
    assert options.append_system_prompt is None


def test_an_untrusted_project_falls_back_to_the_users_own_prompt_files(world: Any):
    (world.pi_home / "agent").mkdir(parents=True)
    (world.pi_home / "agent" / "SYSTEM.md").write_text("Global system", encoding="utf-8")
    (world.pi_home / "agent" / "APPEND_SYSTEM.md").write_text("Global append", encoding="utf-8")

    options = _options(world, CliConfig())

    assert options.custom_prompt == "Global system"
    assert options.append_system_prompt == "Global append"


def test_a_trusted_project_uses_its_own_prompt_files(world: Any):
    options = _options(world, _trusting(world))

    assert options.custom_prompt == "Project system"
    assert options.append_system_prompt == "Project append"


def test_prompts_from_the_users_config_need_no_trust(world: Any):
    config = CliConfig(custom_system_prompt="Mine", append_system_prompt="Mine too")

    options = _options(world, config)

    assert options.custom_prompt == "Mine"
    assert options.append_system_prompt == "Mine too"


def test_context_files_are_loaded_whatever_the_trust(world: Any):
    options = _options(world, CliConfig())

    assert [Path(f.path).name for f in options.context_files or []] == ["AGENTS.md"]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(".pi/skills", True), ("skills", True), ("../shared", True), ("~/skills", False)],
)
def test_which_config_paths_point_into_the_project(raw: str, expected: bool):
    assert is_project_relative_path(raw) is expected


def test_an_absolute_config_path_does_not_point_into_the_project(tmp_path: Path):
    assert is_project_relative_path(str(tmp_path)) is False


@pytest.mark.asyncio
async def test_skills_from_an_untrusted_project_are_not_loaded(world: Any):
    config = CliConfig(skills_dirs=(".pi/skills",))

    resources = await load_session_resources(cwd=world.project, config=config)

    assert _skill_names(resources) == []


@pytest.mark.asyncio
async def test_skills_from_a_trusted_project_are_loaded(world: Any):
    config = _trusting(world, skills_dirs=(".pi/skills",))

    resources = await load_session_resources(cwd=world.project, config=config)

    assert _skill_names(resources) == ["shipped"]


@pytest.mark.asyncio
async def test_the_users_own_skill_directories_load_whatever_the_trust(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    mine = world.tmp / "mine"
    (mine / "own").mkdir(parents=True)
    (mine / "own" / "SKILL.md").write_text(SKILL.format(name="own"), encoding="utf-8")
    (world.home / "tilde" / "viahome").mkdir(parents=True)
    (world.home / "tilde" / "viahome" / "SKILL.md").write_text(
        SKILL.format(name="viahome"), encoding="utf-8"
    )
    config = CliConfig(skills_dirs=(".pi/skills", str(mine), "~/tilde"))

    resources = await load_session_resources(cwd=world.project, config=config)

    assert sorted(_skill_names(resources)) == ["own", "viahome"]  # not "shipped"


# ---------------------------------------------------------------------------
# What the model is actually told
# ---------------------------------------------------------------------------


async def _system_prompt_sent_to_the_llm(
    world: Any, config: CliConfig, *, home: Path | None = None
) -> str:
    seen: list[str] = []

    async def capturing_stream(model, context, options=None):
        seen.append(context.system_prompt)
        return await mock_text_stream(model, context, options)

    repo = JsonlSessionRepo(world.tmp / "sessions")
    session = await repo.create({"cwd": str(world.project)})
    resources = await load_session_resources(cwd=world.project, config=config)
    harness = await create_session_harness(
        session=session,
        cwd=world.project,
        config=config,
        stream_fn=capturing_stream,
        resources=resources,
        home=world.pi_home if home is None else home,
    )
    await harness.prompt("hi")
    return seen[-1]


@pytest.mark.asyncio
async def test_the_llm_is_not_told_what_an_untrusted_project_says(world: Any):
    prompt = await _system_prompt_sent_to_the_llm(world, CliConfig(skills_dirs=(".pi/skills",)))

    assert "Project system" not in prompt
    assert "Project append" not in prompt
    assert "shipped" not in prompt
    assert "Project agents rules" in prompt  # AGENTS.md is not behind the gate


@pytest.mark.asyncio
async def test_the_llm_is_told_what_a_trusted_project_says(world: Any):
    prompt = await _system_prompt_sent_to_the_llm(
        world, _trusting(world, skills_dirs=(".pi/skills",))
    )

    assert "Project system" in prompt
    assert "Project append" in prompt
    assert "<name>shipped</name>" in prompt


@pytest.mark.asyncio
async def test_the_llm_is_told_what_the_home_it_was_given_says(world: Any):
    """An untrusted project falls back to the user's prompt files, from the session's home."""
    elsewhere = world.tmp / "elsewhere"
    (elsewhere / "agent").mkdir(parents=True)
    (elsewhere / "agent" / "APPEND_SYSTEM.md").write_text("Elsewhere append", encoding="utf-8")
    (world.pi_home / "agent").mkdir(parents=True)  # what the default home would have said
    (world.pi_home / "agent" / "APPEND_SYSTEM.md").write_text("Default append", encoding="utf-8")

    prompt = await _system_prompt_sent_to_the_llm(world, CliConfig(), home=elsewhere)

    assert "Elsewhere append" in prompt
    assert "Default append" not in prompt
    assert "Project append" not in prompt


# ---------------------------------------------------------------------------
# A session's own trust (a prompt's answer) overrides what the configuration says
# ---------------------------------------------------------------------------


def test_prompt_files_follow_the_sessions_trust_when_it_is_given(world: Any):
    yes = load_system_prompt_options(
        cwd=world.project, config=CliConfig(), home=world.pi_home, trusted=True
    )
    no = load_system_prompt_options(
        cwd=world.project, config=_trusting(world), home=world.pi_home, trusted=False
    )

    assert (yes.custom_prompt, yes.append_system_prompt) == ("Project system", "Project append")
    assert (no.custom_prompt, no.append_system_prompt) == (None, None)


@pytest.mark.asyncio
async def test_skills_follow_the_sessions_trust_when_it_is_given(world: Any):
    skills = (".pi/skills",)

    yes = await load_session_resources(
        cwd=world.project, config=CliConfig(skills_dirs=skills), trusted=True
    )
    no = await load_session_resources(
        cwd=world.project, config=_trusting(world, skills_dirs=skills), trusted=False
    )

    assert _skill_names(yes) == ["shipped"]
    assert _skill_names(no) == []


@pytest.mark.asyncio
async def test_a_session_harness_follows_the_trust_object_it_is_given(world: Any):
    granted = ProjectTrust()
    granted.grant()
    seen: dict[str, str] = {}

    async def capturing(model, context, options=None):
        seen["prompt"] = context.system_prompt
        return await mock_text_stream(model, context, options)

    for name, trust, config in (
        ("granted", granted, CliConfig()),  # the user said yes; the config had said nothing
        ("withheld", ProjectTrust(), _trusting(world)),  # the config trusts; the session does not
    ):
        session = await JsonlSessionRepo(world.tmp / f"sessions-{name}").create(
            {"cwd": str(world.project)}
        )
        harness = await create_session_harness(
            session=session,
            cwd=world.project,
            config=config,
            stream_fn=capturing,
            home=world.pi_home,
            trust=trust,
        )
        await harness.prompt("hi")
        seen[name] = seen["prompt"]

    assert "Project system" in seen["granted"]
    assert "Project system" not in seen["withheld"]


@pytest.mark.asyncio
async def test_the_system_prompt_picks_up_trust_granted_after_the_session_started(world: Any):
    """The prompt dialog is answered after the harness exists; the next turn must show it."""
    trust = ProjectTrust()
    prompts: list[str] = []

    async def capturing(model, context, options=None):
        prompts.append(context.system_prompt)
        return await mock_text_stream(model, context, options)

    session = await JsonlSessionRepo(world.tmp / "sessions").create({"cwd": str(world.project)})
    harness = await create_session_harness(
        session=session,
        cwd=world.project,
        config=CliConfig(skills_dirs=(".pi/skills",)),
        stream_fn=capturing,
        home=world.pi_home,
        trust=trust,
    )
    await harness.prompt("before")
    trust.grant()
    await harness.set_resources(
        await load_session_resources(
            cwd=world.project, config=CliConfig(skills_dirs=(".pi/skills",)), trusted=True
        )
    )
    await harness.prompt("after")

    assert "Project system" not in prompts[0]
    assert "shipped" not in prompts[0]
    assert "Project system" in prompts[1]
    assert "<name>shipped</name>" in prompts[1]


# ---------------------------------------------------------------------------
# Telling the user what was skipped
# ---------------------------------------------------------------------------


def test_the_skipped_resources_are_the_project_files_that_would_have_applied(world: Any):
    config = CliConfig(skills_dirs=(".pi/skills", "~/nothing-here"))

    skipped = skipped_project_resources(config, world.project)

    assert sorted(p.name for p in skipped) == ["APPEND_SYSTEM.md", "SYSTEM.md", "skills"]


def test_nothing_is_reported_for_a_trusted_project(world: Any):
    config = _trusting(world, skills_dirs=(".pi/skills",))

    assert skipped_project_resources(config, world.project) == []


def test_a_prompt_the_users_config_overrides_is_not_reported(world: Any):
    config = CliConfig(custom_system_prompt="Mine", append_system_prompt_file="~/append.md")

    assert skipped_project_resources(config, world.project) == []


def test_a_missing_project_skill_directory_is_not_reported(world: Any):
    (world.project / ".pi" / "SYSTEM.md").unlink()
    (world.project / ".pi" / "APPEND_SYSTEM.md").unlink()
    config = CliConfig(skills_dirs=("no/such/dir",))

    assert skipped_project_resources(config, world.project) == []


def test_the_notice_lists_skipped_resources_next_to_extensions(world: Any):
    from pi_agent_core.extensions.loader import SkippedExtensions

    extensions = [SkippedExtensions(directory=world.project / "ext", names=("a.py",))]
    resources = skipped_project_resources(CliConfig(), world.project)

    notice = untrusted_project_notice(
        extensions=extensions, resources=resources, cwd=world.project, home=world.pi_home
    )

    assert notice is not None
    assert "a.py" in notice
    assert str(world.project / ".pi" / "SYSTEM.md") in notice
    assert "trusted_projects" in notice


def test_the_notice_works_with_resources_alone(world: Any):
    resources = skipped_project_resources(CliConfig(), world.project)

    notice = untrusted_project_notice(resources=resources, cwd=world.project, home=world.pi_home)

    assert notice is not None
    assert "APPEND_SYSTEM.md" in notice


def test_there_is_no_notice_when_nothing_was_skipped(world: Any):
    assert untrusted_project_notice(cwd=world.project, home=world.pi_home) is None


class _RecordingClient:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        raise AssertionError("no tool should ask for permission here")

    def agent_text(self) -> str:
        return "".join(
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        )


async def _open_session(world: Any, config: CliConfig) -> _RecordingClient:
    agent = PiAcpAgent(stream_fn=mock_text_stream, home=world.pi_home, config=config)
    client = _RecordingClient()
    agent.on_connect(client)
    await agent.new_session(cwd=str(world.project))
    # Notices go out after the session/new response has flushed (Zed drops earlier ones).
    await asyncio.gather(*agent._background_tasks)
    return client


@pytest.mark.asyncio
async def test_the_acp_client_is_told_which_project_resources_were_skipped(world: Any):
    # ``never``: an untrusted project, settled without a question (this client answers none).
    config = CliConfig(
        permission="auto", default_project_trust="never", skills_dirs=(".pi/skills",)
    )
    client = await _open_session(world, config)

    text = client.agent_text()
    assert "SYSTEM.md" in text
    assert "skills" in text
    assert "trusted_projects" in text


@pytest.mark.asyncio
async def test_the_acp_client_hears_nothing_when_the_project_is_trusted(world: Any):
    client = await _open_session(world, _trusting(world, permission="auto"))

    assert client.agent_text() == ""


@pytest.mark.asyncio
async def test_headless_prints_the_notice_to_stderr(
    world: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from pi_agent_cli.headless import run_print

    monkeypatch.setenv("PI_USE_MOCK", "1")

    code = await run_print("hello", cwd=world.project, home=world.pi_home)

    captured = capsys.readouterr()
    assert code == 0
    assert "SYSTEM.md" in captured.err
    assert "SYSTEM.md" not in captured.out
