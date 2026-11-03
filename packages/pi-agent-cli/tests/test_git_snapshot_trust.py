"""The ``<git_status>`` snapshot runs ``git`` in the project, so it sits behind project trust.

``git status`` runs the program that ``core.fsmonitor`` in the repository's own ``.git/config``
names. A project that ships its ``.git`` (an unpacked archive, a copied directory) could so run
code the moment a session started, before anybody was asked. The tests use a real ``git`` and a
hook that only creates a marker file in a temporary directory.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import text_block
from acp.schema import AllowedOutcome, RequestPermissionResponse

from pi_agent_cli import headless
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import TRUST_ENV, ProjectTrust
from pi_agent_cli.factory import create_session_harness
from pi_agent_cli.git_context import snapshot_git_context
from pi_agent_cli.headless import run_print
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_cli.trust_prompt import TRUST_OPTION_ID
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import JsonlSessionRepo

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the fsmonitor hook is a POSIX shell script that needs the executable bit; "
        "git for Windows would run it through its own sh, which was not measured"
    ),
)

HOOK = "#!/bin/sh\n: > {marker}\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _quiet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A git repository whose own ``.git/config`` names an fsmonitor hook. The hook creates
    ``marker`` in the temporary directory and does nothing else."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    marker = tmp_path / "hook-ran.marker"
    hook = tmp_path / "hook.sh"
    hook.write_text(HOOK.format(marker=shlex.quote(str(marker))), encoding="utf-8")
    hook.chmod(0o755)

    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    marker.unlink(missing_ok=True)  # the hook may have run while the repository was made
    _git(project, "config", "core.fsmonitor", shlex.quote(str(hook)))
    return SimpleNamespace(
        tmp=tmp_path,
        home=home,
        pi_home=tmp_path / "pi-home",
        project=project,
        marker=marker,
        hook=hook,
    )


def _ship_prompt_file(world: Any) -> None:
    """Something gated, so that the decision is ``undecided`` (a question) and not ``nothing``."""
    (world.project / ".pi").mkdir()
    (world.project / ".pi" / "SYSTEM.md").write_text("Project system", encoding="utf-8")


class _Llm:
    """A stream_fn that notes the system prompt of each turn."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        self.prompts.append(context.system_prompt)
        return await mock_text_stream(model, context, options)


class _Client:
    """An ACP client that answers the trust question as scripted."""

    def __init__(self, grant: bool) -> None:
        self.grant = grant
        self.requests = 0

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        return None

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        self.requests += 1
        option = TRUST_OPTION_ID if self.grant else "reject-once"
        return RequestPermissionResponse(
            outcome=AllowedOutcome(option_id=option, outcome="selected")
        )


async def _acp_first_prompt(world: Any, client: _Client, config: CliConfig | None = None) -> str:
    llm = _Llm()
    agent = PiAcpAgent(
        stream_fn=llm,
        home=world.pi_home,
        config=config or CliConfig(permission="ask", provider="mock", model_id="mock"),
    )
    agent.on_connect(client)  # type: ignore[arg-type]
    created = await agent.new_session(cwd=str(world.project))
    await asyncio.wait_for(
        asyncio.gather(*list(agent._background_tasks), return_exceptions=True), 10
    )
    await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    return llm.prompts[0]


def test_the_hook_runs_when_git_status_is_run_in_the_repository(world: Any):
    """The control: the marker is a real signal. Without this a passing test below proves
    nothing (a hook that never fires would pass it too)."""
    assert not world.marker.exists()

    snapshot = snapshot_git_context(str(world.project), timeout_seconds=10, max_lines=40)

    assert snapshot is not None
    assert world.marker.exists()


async def test_a_session_in_an_untrusted_project_does_not_run_the_repositorys_git_hook(
    world: Any,
):
    """The session-start path, as the ACP agent drives it: a project that ships nothing gated
    (the decision is ``nothing``, which does not count as trusted)."""
    prompt = await _acp_first_prompt(world, _Client(grant=False))

    assert not world.marker.exists()
    assert "<git_status>" not in prompt


async def test_a_refused_question_leaves_the_hook_alone(world: Any):
    _ship_prompt_file(world)
    client = _Client(grant=False)

    prompt = await _acp_first_prompt(world, client)

    assert client.requests == 1
    assert not world.marker.exists()
    assert "<git_status>" not in prompt
    assert "Project system" not in prompt


async def test_a_trusted_project_still_gets_its_git_block(world: Any):
    """Trust given by the user's own config: the snapshot is as before (and `git` runs the
    hook, which is what trusting the project means)."""
    config = CliConfig(
        permission="ask", provider="mock", model_id="mock", trusted_projects=(str(world.project),)
    )

    prompt = await _acp_first_prompt(world, _Client(grant=False), config)

    assert "<git_status>" in prompt and "Branch: main" in prompt
    assert world.marker.exists()


async def test_a_yes_to_the_question_puts_the_git_block_into_the_first_prompt(world: Any):
    """The first prompt waits for the answer, so a yes is in time for the first system prompt."""
    _ship_prompt_file(world)
    client = _Client(grant=True)

    prompt = await _acp_first_prompt(world, client)

    assert client.requests == 1
    assert "<git_status>" in prompt and "Project system" in prompt


@pytest.mark.asyncio
async def test_trust_granted_mid_session_adds_the_block_when_the_prompt_is_built_again(world: Any):
    """The system prompt is built once and cached. The block follows the session's trust at the
    moment the prompt is built; a grant does not rebuild it by itself (a rebuild happens when
    the harness clears its cache, as ``set_resources`` after a yes does)."""
    trust = ProjectTrust()
    prompts: list[str] = []

    async def capturing(model, context, options=None):
        prompts.append(context.system_prompt)
        return await mock_text_stream(model, context, options)

    session = await JsonlSessionRepo(world.tmp / "sessions").create({"cwd": str(world.project)})
    harness = await create_session_harness(
        session=session,
        cwd=world.project,
        config=CliConfig(),
        stream_fn=capturing,
        home=world.pi_home,
        trust=trust,
    )
    await harness.prompt("before")
    assert not world.marker.exists()

    trust.grant()
    await harness.prompt("cached")
    assert not world.marker.exists()

    harness.invalidate_system_prompt_cache()
    await harness.prompt("after")

    assert "<git_status>" not in prompts[0]
    assert prompts[1] == prompts[0]
    assert "<git_status>" in prompts[2]
    assert world.marker.exists()


def test_the_prompt_options_take_no_snapshot_for_an_untrusted_project(world: Any):
    untrusted = load_system_prompt_options(
        cwd=world.project, config=CliConfig(), home=world.pi_home, trusted=False
    )
    assert untrusted.git_status is None
    assert not world.marker.exists()

    # With no word from the session, the configuration decides, and by default it does not trust.
    by_config = load_system_prompt_options(
        cwd=world.project, config=CliConfig(), home=world.pi_home
    )
    assert by_config.git_status is None
    assert not world.marker.exists()

    trusted = load_system_prompt_options(
        cwd=world.project, config=CliConfig(), home=world.pi_home, trusted=True
    )
    assert trusted.git_status is not None and "Branch: main" in trusted.git_status


def test_git_disabled_stays_off_even_for_a_trusted_project(world: Any):
    options = load_system_prompt_options(
        cwd=world.project, config=CliConfig(git_enabled=False), home=world.pi_home, trusted=True
    )

    assert options.git_status is None
    assert not world.marker.exists()


async def _headless_prompt(world: Any, monkeypatch: pytest.MonkeyPatch, toml: str = "") -> str:
    llm = _Llm()
    monkeypatch.setattr(headless, "default_stream_fn", lambda: llm)
    world.pi_home.mkdir(parents=True, exist_ok=True)
    if toml:
        (world.pi_home / "agent.toml").write_text(toml, encoding="utf-8")
    assert await run_print("hello", cwd=world.project, home=world.pi_home) == 0
    return llm.prompts[0]


async def test_a_headless_run_in_an_untrusted_project_does_not_run_the_hook(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    prompt = await _headless_prompt(world, monkeypatch)

    assert not world.marker.exists()
    assert "<git_status>" not in prompt


async def test_a_headless_run_in_a_trusted_project_keeps_the_block(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    prompt = await _headless_prompt(
        world, monkeypatch, '[extensions]\ndefault_project_trust = "always"\n'
    )

    assert "<git_status>" in prompt
    assert world.marker.exists()
