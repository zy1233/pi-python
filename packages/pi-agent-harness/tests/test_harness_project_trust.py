"""A project's extensions can be trusted after the harness exists, but only until they load.

The ACP agent has to open a session before it may ask the user about the project (a client
drops anything sent for a session it has not been told about), so the harness is built first
and the answer arrives later. Importing an extension is what runs it, and it happens once, so
the answer is accepted exactly up to that moment: afterwards a change would be a lie about
what already ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import Model
from pi_agent_harness import (
    AgentHarness,
    AgentHarnessError,
    LocalExecutionEnv,
    MemorySessionStorage,
    Session,
)

EXTENSION = (
    "def activate(pi):\n"
    "    pi.register_command('shipped', description='ext', handler=lambda args: None)\n"
)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])
    extensions = tmp_path / "project" / ".pi-python" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "shipped.py").write_text(EXTENSION, encoding="utf-8")
    return tmp_path / "project"


async def _harness(project: Path, **kwargs: Any) -> AgentHarness:
    return AgentHarness(
        session=Session(await MemorySessionStorage.create(session_id="trust")),
        model=Model(provider="mock", model_id="m1"),
        stream_fn=mock_text_stream,
        env=LocalExecutionEnv(str(project)),
        auto_discover_extensions=True,
        home=project.parent / "home",
        **kwargs,
    )


def _commands(harness: AgentHarness) -> set[str]:
    return set(harness.extension_registry.get_commands())


async def test_an_untrusted_projects_extensions_stay_out(project: Path):
    harness = await _harness(project)

    await harness.load_extensions()

    assert "shipped" not in _commands(harness)
    assert [s.names for s in harness.skipped_extensions] == [("shipped.py",)]


async def test_trust_given_when_the_harness_is_built_loads_them(project: Path):
    harness = await _harness(project, trust_project_extensions=True)

    await harness.load_extensions()

    assert "shipped" in _commands(harness)


async def test_trust_given_afterwards_but_before_loading_loads_them(project: Path):
    harness = await _harness(project)

    harness.set_trust_project_extensions(True)
    await harness.load_extensions()

    assert "shipped" in _commands(harness)
    assert harness.skipped_extensions == []


async def test_trust_can_be_taken_back_before_loading(project: Path):
    harness = await _harness(project, trust_project_extensions=True)

    harness.set_trust_project_extensions(False)
    await harness.load_extensions()

    assert "shipped" not in _commands(harness)
    assert [s.names for s in harness.skipped_extensions] == [("shipped.py",)]


async def test_trust_given_before_the_first_prompt_applies_to_the_lazy_load(project: Path):
    harness = await _harness(project)
    harness.set_trust_project_extensions(True)

    await harness.prompt("hello")  # nothing called load_extensions() first

    assert "shipped" in _commands(harness)


@pytest.mark.parametrize("trusted", [True, False])
async def test_trust_cannot_change_once_the_extensions_have_loaded(project: Path, trusted: bool):
    harness = await _harness(project)
    await harness.load_extensions()
    before = _commands(harness)

    with pytest.raises(AgentHarnessError) as raised:
        harness.set_trust_project_extensions(trusted)

    assert raised.value.code == "invalid_state"
    assert _commands(harness) == before  # and nothing was imported by asking


async def test_a_refused_change_leaves_the_earlier_choice_in_force(project: Path):
    harness = await _harness(project, trust_project_extensions=True)
    await harness.load_extensions()

    with pytest.raises(AgentHarnessError):
        harness.set_trust_project_extensions(False)

    assert "shipped" in _commands(harness)
    assert harness.skipped_extensions == []
