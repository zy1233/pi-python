"""Headless (``-p``) runs cannot ask about a project, but they honour what was decided before.

Upstream pi never prompts in a non-interactive mode. Here, nobody is asked, yet an answer given
earlier in an ACP session ("Trust and remember") still counts while the project's files are the
ones it was about; and a run that leaves the project's resources out says why.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pi_agent_cli import headless
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import TRUST_ENV, decide_project_trust
from pi_agent_cli.headless import run_print
from pi_agent_cli.trust_store import TrustStore
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream

SHIPPED = (
    "from pathlib import Path\n"
    "Path({marker!r}).write_text('imported')\n"
    "def activate(pi):\n"
    "    pass\n"
)


class _Llm:
    """A stream_fn that notes the system prompt of each turn."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        self.prompts.append(context.system_prompt)
        return await mock_text_stream(model, context, options)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An empty HOME and a project that ships an extension (whose *import* leaves a marker file
    behind) and a system prompt. The model is a recording mock."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])
    monkeypatch.delenv(TRUST_ENV, raising=False)
    llm = _Llm()
    monkeypatch.setattr(headless, "default_stream_fn", lambda: llm)

    project = tmp_path / "project"
    extensions = project / ".pi-python" / "extensions"
    extensions.mkdir(parents=True)
    marker = tmp_path / "imported.marker"
    shipped = extensions / "shipped.py"
    shipped.write_text(SHIPPED.format(marker=str(marker)), encoding="utf-8")
    (project / ".pi").mkdir()
    (project / ".pi" / "SYSTEM.md").write_text("Project system", encoding="utf-8")
    return SimpleNamespace(
        home=home,
        # Not the default home: code that forgets to pass the given home on would look in
        # ``home / ".pi-python"`` and find nothing, instead of the right place by coincidence.
        pi_home=tmp_path / "pi-home",
        project=project,
        marker=marker,
        shipped=shipped,
        llm=llm,
    )


def _remember_current_files(world: Any, **config: Any) -> None:
    """What "Trust and remember" stores: the project, against its files as they are now."""
    decision = decide_project_trust(CliConfig(**config), world.project, home=world.pi_home)
    assert decision.fingerprint is not None
    TrustStore(world.pi_home).remember(world.project, decision.fingerprint)


def _write_config(world: Any, body: str) -> None:
    world.pi_home.mkdir(parents=True, exist_ok=True)
    (world.pi_home / "agent.toml").write_text(body, encoding="utf-8")


async def _run(world: Any, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = await run_print("hello", cwd=world.project, home=world.pi_home)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


async def test_an_answer_given_earlier_is_honoured(world: Any, capsys: pytest.CaptureFixture[str]):
    _remember_current_files(world)

    code, out, err = await _run(world, capsys)

    assert code == 0
    assert world.marker.exists()  # the extension was imported
    assert "Project system" in world.llm.prompts[-1]  # and the model was told what it says
    assert "Skipped project resources" not in err
    assert "Hello from mock" in out


async def test_an_answer_about_other_files_is_not_honoured(
    world: Any, capsys: pytest.CaptureFixture[str]
):
    _remember_current_files(world)
    world.shipped.write_text(
        world.shipped.read_text(encoding="utf-8") + "# changed by a pull\n", encoding="utf-8"
    )

    code, out, err = await _run(world, capsys)

    assert code == 0
    assert not world.marker.exists()
    assert "Project system" not in world.llm.prompts[-1]
    assert "shipped.py" in err
    assert "changed since you last trusted it" in err
    assert "Hello from mock" in out  # the run carries on without the project's resources


async def test_nobody_is_asked_and_the_run_says_the_project_is_not_trusted(
    world: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    def no_input(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("a headless run must never wait for an answer")

    monkeypatch.setattr("builtins.input", no_input)

    code, _, err = await _run(world, capsys)

    assert code == 0
    assert not world.marker.exists()
    assert "Project system" not in world.llm.prompts[-1]
    assert "shipped.py" in err
    assert "this project is not trusted" in err
    assert "trusted_projects" in err  # how to turn them on
    assert TrustStore(world.pi_home).get(world.project) is None  # nothing was decided for them


async def test_never_is_reported_as_the_reason(world: Any, capsys: pytest.CaptureFixture[str]):
    _write_config(world, '[extensions]\ndefault_project_trust = "never"\n')

    code, _, err = await _run(world, capsys)

    assert code == 0
    assert not world.marker.exists()
    assert "`default_project_trust` is set to `never`" in err


async def test_always_trusts_the_project(world: Any, capsys: pytest.CaptureFixture[str]):
    _write_config(world, '[extensions]\ndefault_project_trust = "always"\n')

    code, _, err = await _run(world, capsys)

    assert code == 0
    assert world.marker.exists()
    assert "Project system" in world.llm.prompts[-1]
    assert "Skipped project resources" not in err


async def test_the_projects_skills_follow_the_answer_too(
    world: Any, capsys: pytest.CaptureFixture[str]
):
    skill = world.project / ".pi" / "skills" / "guide"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: guide\ndescription: guide skill\n---\nBody\n", encoding="utf-8"
    )
    _write_config(world, '[skills]\npaths = [".pi/skills"]\n')

    await _run(world, capsys)
    assert "<name>guide</name>" not in world.llm.prompts[-1]  # nobody has vouched for it

    _remember_current_files(world, skills_dirs=(".pi/skills",))
    await _run(world, capsys)
    assert "<name>guide</name>" in world.llm.prompts[-1]


async def test_the_given_home_is_where_the_users_own_extensions_are_found(
    world: Any, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "extensions").mkdir(parents=True)
    marker = tmp_path / "mine.marker"
    (elsewhere / "extensions" / "mine.py").write_text(
        SHIPPED.format(marker=str(marker)), encoding="utf-8"
    )

    code = await run_print("hello", cwd=world.project, home=elsewhere)
    capsys.readouterr()

    assert code == 0
    assert marker.exists()


async def test_the_projects_saved_workflows_are_reported_as_left_out(
    world: Any, capsys: pytest.CaptureFixture[str]
):
    """They are what the dynamic-workflows extension would have turned into slash commands
    (audit F7-01); a headless run has none, but the notice is the same as for the rest."""
    workflows = world.project / ".pi-python" / "workflows"
    workflows.mkdir()
    (workflows / "review.py").write_text("meta = {}\n", encoding="utf-8")

    code, _, err = await _run(world, capsys)

    assert code == 0
    assert str(workflows) in err


async def test_an_answer_about_other_workflows_is_not_honoured(
    world: Any, capsys: pytest.CaptureFixture[str]
):
    workflows = world.project / ".pi-python" / "workflows"
    workflows.mkdir()
    script = workflows / "review.py"
    script.write_text("meta = {}\n", encoding="utf-8")
    _remember_current_files(world)
    script.write_text("meta = {'description': 'added by a pull'}\n", encoding="utf-8")

    code, _, err = await _run(world, capsys)

    assert code == 0
    assert not world.marker.exists()
    assert "changed since you last trusted it" in err
    assert str(workflows) in err


async def test_started_in_the_directory_that_holds_the_pi_home_the_workflows_are_the_users(
    world: Any, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    """``<cwd>/.pi-python/workflows`` is ``<pi home>/workflows`` here: nothing of the
    project's, so nothing is reported as left out."""
    bare = tmp_path / "bare"
    (bare / ".pi-python" / "workflows").mkdir(parents=True)
    (bare / ".pi-python" / "workflows" / "mine.py").write_text("meta = {}\n", encoding="utf-8")

    code = await run_print("hello", cwd=bare, home=bare / ".pi-python")
    captured = capsys.readouterr()

    assert code == 0
    assert "Skipped project resources" not in captured.err
