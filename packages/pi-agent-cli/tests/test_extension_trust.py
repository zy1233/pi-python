"""Project-local extensions are opt-in (audit P7-02).

``<cwd>/.pi-python/extensions`` ships with the repository, and importing it executes its code
the moment a session opens. The CLI therefore trusts a project only when the user said so in
a place the project cannot write to: ``~/.pi-python/agent.toml`` (``[extensions]``), the
``PI_TRUST_PROJECT_EXTENSIONS`` environment variable, or ``--trust-project-extensions``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig, load_config
from pi_agent_cli.extension_trust import (
    TRUST_ENV,
    ProjectTrust,
    project_extensions_trusted,
    untrusted_project_notice,
)
from pi_agent_cli.factory import create_session_harness
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.extensions.loader import SkippedExtensions
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import JsonlSessionRepo


@pytest.fixture(autouse=True)
def _clean_trust_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An empty HOME, no installed extensions, and a project that ships one extension whose
    *import* leaves a marker file behind."""
    from types import SimpleNamespace

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])

    project = tmp_path / "project"
    extensions = project / ".pi-python" / "extensions"
    extensions.mkdir(parents=True)
    marker = tmp_path / "imported.marker"
    (extensions / "shipped.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def activate(pi):\n"
        "    pi.register_command('shipped', description='ext', handler=lambda args: None)\n",
        encoding="utf-8",
    )
    return SimpleNamespace(
        tmp=tmp_path, home=home, pi_home=home / ".pi-python", project=project, marker=marker
    )


async def _harness(world: Any, config: CliConfig):
    repo = JsonlSessionRepo(world.tmp / "sessions")
    session = await repo.create({"cwd": str(world.project)})
    harness = await create_session_harness(
        session=session,
        cwd=world.project,
        config=config,
        stream_fn=mock_text_stream,
        home=world.pi_home,
    )
    await harness.load_extensions()
    return harness


# ---------------------------------------------------------------------------
# Config: [extensions]
# ---------------------------------------------------------------------------


def test_extensions_are_untrusted_by_default(tmp_path: Path):
    config = load_config(tmp_path)

    assert config.trust_project_extensions is False
    assert config.trusted_projects == ()


def test_extensions_table_parses_the_switch_and_the_allow_list(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        '[extensions]\ntrust_project_extensions = true\ntrusted_projects = ["/a/b", "~/c", "  "]\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.trust_project_extensions is True
    assert config.trusted_projects == ("/a/b", "~/c")  # blank entries dropped


def test_malformed_extensions_table_stays_untrusted(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        '[extensions]\ntrust_project_extensions = "maybe"\ntrusted_projects = "/not/a/list"\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.trust_project_extensions is False
    assert config.trusted_projects == ()


def test_the_default_project_trust_is_not_set_unless_the_user_sets_it(tmp_path: Path):
    assert load_config(tmp_path).default_project_trust is None
    assert CliConfig().default_project_trust is None


@pytest.mark.parametrize("value", ["ask", "never", "always", "Never", " ALWAYS "])
def test_the_default_project_trust_parses(tmp_path: Path, value: str):
    (tmp_path / "agent.toml").write_text(
        f'[extensions]\ndefault_project_trust = "{value}"\n', encoding="utf-8"
    )

    assert load_config(tmp_path).default_project_trust == value.strip().lower()


@pytest.mark.parametrize("raw", ['"sometimes"', "true", "1", '""', '["ask"]'])
def test_an_unrecognised_default_project_trust_is_ignored(tmp_path: Path, raw: str):
    """A typo must not silently pick a side: it is as if the key were absent."""
    (tmp_path / "agent.toml").write_text(
        f"[extensions]\ndefault_project_trust = {raw}\n", encoding="utf-8"
    )

    assert load_config(tmp_path).default_project_trust is None


# ---------------------------------------------------------------------------
# Decision: project_extensions_trusted
# ---------------------------------------------------------------------------


def test_nothing_is_trusted_without_being_asked(tmp_path: Path):
    assert project_extensions_trusted(CliConfig(), tmp_path) is False


def test_the_global_switch_trusts_every_project(tmp_path: Path):
    assert project_extensions_trusted(CliConfig(trust_project_extensions=True), tmp_path) is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_environment_variable_trusts_every_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
):
    monkeypatch.setenv(TRUST_ENV, value)

    assert project_extensions_trusted(CliConfig(), tmp_path) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_other_environment_values_do_not_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
):
    monkeypatch.setenv(TRUST_ENV, value)

    assert project_extensions_trusted(CliConfig(), tmp_path) is False


def test_an_allow_listed_project_is_trusted(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()

    assert project_extensions_trusted(CliConfig(trusted_projects=(str(project),)), project)


def test_a_directory_inside_an_allow_listed_project_is_trusted(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "pkg" / "sub").mkdir(parents=True)

    config = CliConfig(trusted_projects=(str(project),))

    assert project_extensions_trusted(config, project / "pkg" / "sub")


def test_the_parent_of_an_allow_listed_project_is_not_trusted(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()

    assert not project_extensions_trusted(CliConfig(trusted_projects=(str(project),)), tmp_path)


def test_a_sibling_sharing_a_name_prefix_is_not_trusted(tmp_path: Path):
    trusted = tmp_path / "proj"
    sibling = tmp_path / "proj-evil"
    trusted.mkdir()
    sibling.mkdir()

    assert not project_extensions_trusted(CliConfig(trusted_projects=(str(trusted),)), sibling)


def test_a_relative_allow_list_entry_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Resolved against the process cwd it would trust whatever the user happened to open
    the agent from; only absolute (or ``~``) entries mean anything."""
    (tmp_path / "project").mkdir()
    monkeypatch.chdir(tmp_path)

    assert not project_extensions_trusted(
        CliConfig(trusted_projects=("project",)), tmp_path / "project"
    )


def test_a_tilde_entry_expands_to_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    project = tmp_path / "work" / "proj"
    project.mkdir(parents=True)

    assert project_extensions_trusted(CliConfig(trusted_projects=("~/work/proj",)), project)


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_allow_list_matching_ignores_case_and_slash_style_on_windows(tmp_path: Path):
    project = tmp_path / "Proj"
    project.mkdir()
    entry = str(project).upper().replace("\\", "/")

    assert project_extensions_trusted(CliConfig(trusted_projects=(entry,)), project)


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this environment cannot create symlinks")


def test_a_symlink_inside_an_allow_listed_project_does_not_lend_it_trust(tmp_path: Path):
    """The extensions that would run live where the link points, so that is the path the
    allow-list has to vouch for, not the path the link happens to sit under."""
    trusted = tmp_path / "trusted"
    elsewhere = tmp_path / "elsewhere"
    trusted.mkdir()
    elsewhere.mkdir()
    _symlink(trusted / "vendored", elsewhere)

    config = CliConfig(trusted_projects=(str(trusted),))

    assert not project_extensions_trusted(config, trusted / "vendored")


def test_a_symlink_to_an_allow_listed_project_is_trusted(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    _symlink(alias, real)

    assert project_extensions_trusted(CliConfig(trusted_projects=(str(real),)), alias)


# ---------------------------------------------------------------------------
# Wiring: create_session_harness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_project_extension_is_not_run_by_default(world: Any):
    harness = await _harness(world, CliConfig())

    assert not world.marker.exists()  # the repository's code never ran
    assert "shipped" not in harness.extension_registry.get_commands()
    ((skipped),) = harness.skipped_extensions
    assert skipped.names == ("shipped.py",)


@pytest.mark.asyncio
async def test_an_allow_listed_project_runs_its_extension(world: Any):
    harness = await _harness(world, CliConfig(trusted_projects=(str(world.project),)))

    assert world.marker.exists()
    assert "shipped" in harness.extension_registry.get_commands()
    assert harness.skipped_extensions == []


@pytest.mark.asyncio
async def test_the_global_switch_runs_project_extensions(world: Any):
    harness = await _harness(world, CliConfig(trust_project_extensions=True))

    assert "shipped" in harness.extension_registry.get_commands()


@pytest.mark.asyncio
async def test_the_environment_variable_runs_project_extensions(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(TRUST_ENV, "1")

    harness = await _harness(world, CliConfig())

    assert "shipped" in harness.extension_registry.get_commands()


async def _harness_with_trust(world: Any, config: CliConfig, trust: ProjectTrust):
    repo = JsonlSessionRepo(world.tmp / "sessions")
    session = await repo.create({"cwd": str(world.project)})
    harness = await create_session_harness(
        session=session,
        cwd=world.project,
        config=config,
        stream_fn=mock_text_stream,
        home=world.pi_home,
        trust=trust,
    )
    return harness


@pytest.mark.asyncio
async def test_a_session_that_was_trusted_by_a_prompt_runs_the_extension(world: Any):
    trust = ProjectTrust()
    trust.grant()
    harness = await _harness_with_trust(world, CliConfig(), trust)

    await harness.load_extensions()

    assert world.marker.exists()
    assert "shipped" in harness.extension_registry.get_commands()


@pytest.mark.asyncio
async def test_the_configuration_cannot_trust_what_the_session_has_not(world: Any):
    harness = await _harness_with_trust(
        world, CliConfig(trust_project_extensions=True), ProjectTrust()
    )

    await harness.load_extensions()

    assert not world.marker.exists()
    assert len(harness.skipped_extensions) == 1


@pytest.mark.asyncio
async def test_an_answer_that_arrives_before_the_extensions_load_is_honoured(world: Any):
    """The prompt is answered after the harness exists and before extensions load."""
    trust = ProjectTrust()
    harness = await _harness_with_trust(world, CliConfig(), trust)
    assert not world.marker.exists()

    trust.grant()
    harness.set_trust_project_extensions(trust.trusted)
    await harness.load_extensions()

    assert world.marker.exists()


@pytest.mark.asyncio
async def test_trusting_another_project_does_not_trust_this_one(world: Any):
    other = world.tmp / "other"
    other.mkdir()

    harness = await _harness(world, CliConfig(trusted_projects=(str(other),)))

    assert not world.marker.exists()
    assert len(harness.skipped_extensions) == 1


@pytest.mark.asyncio
async def test_a_project_cannot_trust_itself_through_its_own_config(world: Any):
    """Trust comes from the user's home. A config file shipped inside the project must not
    be able to grant it, or the guard would be one ``git clone`` away from useless."""
    (world.project / ".pi-python" / "agent.toml").write_text(
        "[extensions]\n"
        "trust_project_extensions = true\n"
        f'trusted_projects = ["{world.project.as_posix()}"]\n',
        encoding="utf-8",
    )

    harness = await _harness(world, load_config(world.pi_home))

    assert not world.marker.exists()
    assert len(harness.skipped_extensions) == 1


# ---------------------------------------------------------------------------
# Telling the user what was skipped
# ---------------------------------------------------------------------------


def test_the_notice_says_what_was_skipped_and_how_to_enable_it(tmp_path: Path):
    skipped = [
        SkippedExtensions(directory=tmp_path / ".pi-python" / "extensions", names=("a.py", "pkg"))
    ]

    notice = untrusted_project_notice(extensions=skipped, cwd=tmp_path, home=tmp_path / "home")

    assert notice is not None
    assert str(tmp_path / ".pi-python" / "extensions") in notice
    assert "a.py" in notice and "pkg" in notice
    assert "trusted_projects" in notice
    assert str(tmp_path / "home" / "agent.toml") in notice  # where to put it
    assert "--trust-project-extensions" in notice
    assert TRUST_ENV in notice


def test_there_is_no_notice_when_nothing_was_skipped(tmp_path: Path):
    assert untrusted_project_notice(cwd=tmp_path, home=tmp_path) is None


def test_the_suggested_allow_list_entry_can_be_pasted_into_agent_toml(tmp_path: Path):
    """On Windows a raw ``C:\\Users\\...`` inside a TOML double-quoted string is a syntax error
    (``\\U`` is not an escape), which would stop the agent from starting."""
    import re
    import tomllib

    project = tmp_path / "proj"
    project.mkdir()
    skipped = [SkippedExtensions(directory=project / ".pi-python" / "extensions", names=("a.py",))]

    notice = untrusted_project_notice(extensions=skipped, cwd=project, home=tmp_path)

    assert notice is not None
    match = re.search(r'add "([^"]+)" to `trusted_projects`', notice)
    assert match is not None
    entry = tomllib.loads(f'trusted_projects = ["{match.group(1)}"]')["trusted_projects"][0]
    assert project_extensions_trusted(CliConfig(trusted_projects=(entry,)), project)


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
async def test_the_acp_client_is_told_which_project_extensions_were_skipped(world: Any):
    # ``never``: an untrusted project, settled without a question (this client answers none).
    client = await _open_session(world, CliConfig(permission="auto", default_project_trust="never"))

    text = client.agent_text()
    assert "shipped.py" in text
    assert "trusted_projects" in text
    assert not world.marker.exists()


@pytest.mark.asyncio
async def test_the_acp_client_hears_nothing_when_the_project_is_trusted(world: Any):
    config = CliConfig(permission="auto", trusted_projects=(str(world.project),))

    client = await _open_session(world, config)

    assert "shipped.py" not in client.agent_text()
    assert world.marker.exists()


@pytest.mark.asyncio
async def test_the_acp_client_hears_nothing_when_there_is_nothing_to_skip(world: Any):
    (world.project / ".pi-python" / "extensions" / "shipped.py").unlink()

    client = await _open_session(world, CliConfig(permission="auto"))

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
    assert "Hello from mock" in captured.out  # the answer stays alone on stdout
    assert "shipped.py" in captured.err
    assert not world.marker.exists()


# ---------------------------------------------------------------------------
# --trust-project-extensions
# ---------------------------------------------------------------------------


def _run_cli(world: Any, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop(TRUST_ENV, None)
    env.update(
        PI_USE_MOCK="1",
        PI_HOME=str(world.pi_home),
        HOME=str(world.home),
        USERPROFILE=str(world.home),
    )
    return subprocess.run(
        [sys.executable, "-m", "pi_agent_cli", "-p", "hello", "--cwd", str(world.project), *extra],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_the_cli_does_not_run_project_extensions_without_the_flag(world: Any):
    result = _run_cli(world)

    assert result.returncode == 0, result.stderr
    assert not world.marker.exists()
    assert "shipped.py" in result.stderr


def test_the_cli_flag_trusts_project_extensions(world: Any):
    result = _run_cli(world, "--trust-project-extensions")

    assert result.returncode == 0, result.stderr
    assert world.marker.exists()
    assert "shipped.py" not in result.stderr


def test_the_flag_is_not_headless_only():
    """It has to work for the ACP agent (the TUI spawns) as well."""
    from pi_agent_cli.__main__ import _build_parser, _has_prompt_cli_flags

    args = _build_parser().parse_args(["--trust-project-extensions"])

    assert args.trust_project_extensions is True
    assert not _has_prompt_cli_flags(args)
