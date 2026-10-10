"""A project's saved workflows sit behind project trust, and one bad ``meta`` cannot break a
session (audit F7-01, P7-17).

``<project>/.pi-python/workflows`` ships with the repository. The dynamic-workflows extension
turns every script there into a slash command whose description the repository wrote, and the
command's handler tells the model to run that script. So a saved workflow is the project
speaking for the user, like its extensions, prompt files and skills are, and gets the same
treatment: left out unless the user vouched for the project, asked about in the same question,
and bound to the same fingerprint, so a ``git pull`` that changes one asks again.

The user's own workflows (``<pi home>/workflows``) need no trust.

A script's ``meta`` is the repository's text as well. A ``description`` that is not text used to
make the ``available_commands_update`` invalid, and since that update is sent again with every
prompt until it succeeds, no ``session/prompt`` worked at all.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import text_block
from acp.schema import AllowedOutcome, RequestPermissionResponse

from pi_agent_cli.agent import PiAcpAgent, _why_invalid
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import (
    TRUST_ENV,
    decide_project_trust,
    skipped_project_resources,
    untrusted_project_notice,
)
from pi_agent_cli.trust_prompt import REJECT_OPTION_ID, TRUST_OPTION_ID
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.extensions.types import CommandDef
from pi_agent_core.tests.mock_stream import mock_text_stream

pytest.importorskip("pi_dynamic_workflows")
from pi_dynamic_workflows import activate as workflows_activate

TRUST = AllowedOutcome(option_id=TRUST_OPTION_ID, outcome="selected")
REFUSE = AllowedOutcome(option_id=REJECT_OPTION_ID, outcome="selected")

OWN_DESCRIPTION = "List and manage dynamic workflows"  # what the extension's own command says


def _script(name: str, description: Any = "A workflow") -> str:
    """A saved workflow whose ``meta`` is a Python literal (``True``, not JSON's ``true``)."""
    meta = {"name": name, "description": description}
    return f"meta = {meta!r}\nasync def main():\n    result('ok')\n"


@pytest.fixture(autouse=True)
def _quiet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An empty HOME, the workflows extension as the only one installed, and a project that
    ships one saved workflow (``shipped``)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [workflows_activate])

    project = tmp_path / "project"
    workflows = project / ".pi-python" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "shipped.py").write_text(_script("shipped", "From the project"), encoding="utf-8")
    # Not the default home: code that forgets to pass the agent's home on would look in
    # ``home / ".pi-python"`` and find nothing, instead of the right place by coincidence.
    pi_home = tmp_path / "pi-home"
    return SimpleNamespace(
        tmp=tmp_path,
        home=home,
        pi_home=pi_home,
        user_workflows=pi_home / "workflows",
        project=project,
        workflows=workflows,
    )


def _save_user_workflow(world: Any, name: str, description: Any = "A workflow") -> None:
    world.user_workflows.mkdir(parents=True, exist_ok=True)
    (world.user_workflows / f"{name}.py").write_text(_script(name, description), encoding="utf-8")


class _Client:
    """An ACP client: notes what it is sent, and answers the trust question as scripted."""

    def __init__(self, answer: Any = TRUST) -> None:
        self.updates: list[Any] = []
        self.requests: list[SimpleNamespace] = []
        self.answer = answer

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        self.requests.append(SimpleNamespace(tool_call=tool_call, options=options))
        return RequestPermissionResponse(outcome=self.answer)

    def text(self) -> str:
        return "".join(
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        )

    def commands(self) -> set[str]:
        names: set[str] = set()
        for update in self.updates:
            if getattr(update, "session_update", None) == "available_commands_update":
                names.update(command.name for command in update.available_commands)
        return names


async def _idle(agent: PiAcpAgent) -> None:
    """Wait for the deferred setup after ``session/new``; a task that failed is an error."""
    results = await asyncio.wait_for(
        asyncio.gather(*list(agent._background_tasks), return_exceptions=True), 10
    )
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            raise result


async def _open(world: Any, client: _Client, config: CliConfig | None = None):
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=world.pi_home,
        config=config or CliConfig(permission="ask", provider="mock", model_id="mock"),
    )
    agent.on_connect(client)  # type: ignore[arg-type]
    created = await agent.new_session(cwd=str(world.project))
    await _idle(agent)
    return agent, created.session_id


def _commands(agent: PiAcpAgent, session_id: str) -> dict[str, CommandDef]:
    return agent._harnesses[session_id].extension_registry.get_commands()


async def _prompt(agent: PiAcpAgent, session_id: str) -> str:
    response = await agent.prompt(session_id=session_id, prompt=[text_block("hi")])
    return response.stop_reason


# ---------------------------------------------------------------------------
# F7-01: the project's saved workflows are behind project trust
# ---------------------------------------------------------------------------


async def test_an_untrusted_projects_saved_workflows_are_not_commands(world: Any):
    client = _Client(REFUSE)

    agent, session_id = await _open(world, client)

    assert "shipped" not in _commands(agent, session_id)
    assert "shipped" not in client.commands()
    assert "workflows" in client.commands()  # the extension itself is there


async def test_the_user_is_asked_about_the_saved_workflows(world: Any):
    client = _Client(REFUSE)

    await _open(world, client)

    (request,) = client.requests
    assert request.tool_call.title == "loading this project's saved workflows"
    assert request.tool_call.raw_input["resources"] == [
        "workflows: .pi-python/workflows (shipped.py)"
    ]
    assert "workflows: `.pi-python/workflows` (`shipped.py`)" in client.text()


async def test_a_project_that_ships_workflows_and_more_is_asked_once_about_all_of_it(world: Any):
    (world.project / ".pi").mkdir()
    (world.project / ".pi" / "SYSTEM.md").write_text("Project system", encoding="utf-8")
    client = _Client(REFUSE)

    await _open(world, client)

    (request,) = client.requests
    assert request.tool_call.title == "loading this project's saved workflows and prompt files"


async def test_trusting_the_project_registers_its_saved_workflows(world: Any):
    client = _Client(TRUST)

    agent, session_id = await _open(world, client)

    assert _commands(agent, session_id)["shipped"].description == "From the project"
    assert "shipped" in client.commands()


async def test_the_answer_is_remembered_against_the_workflows_too(world: Any):
    client = _Client(TRUST)
    await _open(world, client)
    config = CliConfig()
    assert decide_project_trust(config, world.project, home=world.pi_home).reason == "saved"

    (world.workflows / "shipped.py").write_text(
        _script("shipped", "From the project") + "# changed by a pull\n", encoding="utf-8"
    )

    decision = decide_project_trust(config, world.project, home=world.pi_home)
    assert decision.reason == "changed"  # a pull that edits a workflow asks again


async def test_a_workflow_added_by_a_pull_asks_again(world: Any):
    client = _Client(TRUST)
    await _open(world, client)

    (world.workflows / "extra.py").write_text(_script("extra"), encoding="utf-8")

    decision = decide_project_trust(CliConfig(), world.project, home=world.pi_home)
    assert decision.reason == "changed"


async def test_a_project_script_cannot_take_the_place_of_the_workflows_command(world: Any):
    """Even a trusted project: the extension's own ``/workflows`` is not a saved workflow's
    to replace."""
    (world.workflows / "workflows.py").write_text(_script("workflows", "SHADOW"), encoding="utf-8")
    client = _Client(TRUST)

    agent, session_id = await _open(world, client)

    assert _commands(agent, session_id)["workflows"].description == OWN_DESCRIPTION


async def test_the_users_own_workflows_need_no_trust(world: Any):
    _save_user_workflow(world, "mine", "From the user")
    client = _Client(REFUSE)

    agent, session_id = await _open(world, client)

    commands = _commands(agent, session_id)
    assert commands["mine"].description == "From the user"
    assert "shipped" not in commands


async def test_a_refused_project_cannot_replace_a_workflow_of_the_user_by_name(world: Any):
    _save_user_workflow(world, "shipped", "Mine")
    client = _Client(REFUSE)

    agent, session_id = await _open(world, client)

    assert _commands(agent, session_id)["shipped"].description == "Mine"


async def test_a_trusted_project_overrides_the_users_workflow_of_the_same_name(world: Any):
    """Kept: the project's copy wins once the user has vouched for the project."""
    _save_user_workflow(world, "shipped", "Mine")
    client = _Client(TRUST)

    agent, session_id = await _open(world, client)

    assert _commands(agent, session_id)["shipped"].description == "From the project"


async def test_a_project_in_the_users_allow_list_needs_no_question(world: Any):
    client = _Client(REFUSE)
    config = CliConfig(
        permission="ask", provider="mock", model_id="mock", trusted_projects=(str(world.project),)
    )

    agent, session_id = await _open(world, client, config)

    assert client.requests == []
    assert "shipped" in _commands(agent, session_id)


async def test_started_in_the_directory_that_holds_the_pi_home_the_workflows_are_the_users(
    world: Any,
):
    """``<cwd>/.pi-python/workflows`` is also ``<pi home>/workflows`` here: nothing of the
    project's to ask about, and nothing to report as left out."""
    world.pi_home = world.project / ".pi-python"
    client = _Client(REFUSE)

    agent, session_id = await _open(world, client)

    assert client.requests == []
    assert "Skipped project resources" not in client.text()
    assert "shipped" in _commands(agent, session_id)


# ---------------------------------------------------------------------------
# F7-01: what is told to the user when they are left out
# ---------------------------------------------------------------------------


def test_the_skipped_resources_include_the_workflows_directory(world: Any):
    skipped = skipped_project_resources(CliConfig(), world.project, home=world.pi_home)

    assert skipped == [world.workflows.resolve()]


def test_nothing_is_reported_for_a_trusted_project(world: Any):
    assert (
        skipped_project_resources(CliConfig(), world.project, trusted=True, home=world.pi_home)
        == []
    )


def test_the_users_own_directory_is_not_reported_when_the_project_is_the_home(world: Any):
    """Run from the directory that holds the pi home, ``.pi-python/workflows`` is the user's."""
    pi_home = world.project / ".pi-python"

    assert skipped_project_resources(CliConfig(), world.project, home=pi_home) == []


def test_the_notice_says_what_a_saved_workflow_is_and_where_it_was_left_out(world: Any):
    resources = skipped_project_resources(CliConfig(), world.project, home=world.pi_home)

    notice = untrusted_project_notice(resources=resources, cwd=world.project, home=world.pi_home)

    assert notice is not None
    assert str(world.workflows.resolve()) in notice
    assert "saved workflows" in notice
    assert "trusted_projects" in notice  # and how to turn them on


async def test_the_untrusted_project_notice_reaches_an_acp_client(world: Any):
    client = _Client(REFUSE)

    await _open(world, client)

    assert "Skipped project resources" in client.text()
    assert "saved workflows" in client.text()


# ---------------------------------------------------------------------------
# P7-17: a ``meta`` that is not what it should be cannot break the session
# ---------------------------------------------------------------------------

ODD_DESCRIPTIONS = [5, True, ["a"], {"x": 1}, 1.5]


@pytest.mark.parametrize("odd", ODD_DESCRIPTIONS, ids=repr)
async def test_a_description_that_is_not_text_does_not_break_the_session(world: Any, odd: Any):
    _save_user_workflow(world, "odd", odd)
    client = _Client(REFUSE)

    agent, session_id = await _open(world, client)

    assert {"workflows", "odd"} <= client.commands()  # advertised, with the default text
    assert _commands(agent, session_id)["odd"].description == "Run saved workflow: odd"
    assert await _prompt(agent, session_id) == "end_turn"


@pytest.mark.parametrize("odd", ODD_DESCRIPTIONS, ids=repr)
async def test_the_same_holds_for_a_workflow_the_project_ships(world: Any, odd: Any):
    (world.workflows / "odd.py").write_text(_script("odd", odd), encoding="utf-8")
    client = _Client(TRUST)

    agent, session_id = await _open(world, client)

    assert {"workflows", "odd", "shipped"} <= client.commands()
    assert await _prompt(agent, session_id) == "end_turn"


async def test_a_command_that_cannot_be_advertised_is_left_out_and_the_rest_are_not(
    world: Any, caplog: pytest.LogCaptureFixture
):
    """Whatever puts a command with an invalid description into the registry (an extension
    that skips ``register_command``'s check, say), the others are still advertised and the
    prompt that re-advertises them still works."""
    client = _Client(REFUSE)
    agent, session_id = await _open(world, client)
    broken = CommandDef(name="broken", description=5, handler=lambda args: None)  # type: ignore[arg-type]
    agent._harnesses[session_id].extension_registry.add_command(broken)
    agent._commands_advertised.discard(session_id)
    client.updates.clear()

    with caplog.at_level(logging.WARNING, logger="pi_agent_cli.agent"):
        stop_reason = await _prompt(agent, session_id)

    assert stop_reason == "end_turn"
    assert "workflows" in client.commands()
    assert "broken" not in client.commands()
    assert any("broken" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


async def test_a_session_is_marked_as_advertised_once_the_rest_went_out(world: Any):
    client = _Client(REFUSE)
    agent, session_id = await _open(world, client)
    broken = CommandDef(name="broken", description=None, handler=lambda args: None)  # type: ignore[arg-type]
    agent._harnesses[session_id].extension_registry.add_command(broken)
    agent._commands_advertised.discard(session_id)

    await agent._advertise_commands(session_id)

    assert session_id in agent._commands_advertised  # not retried, and not warned about, again


async def test_the_warning_names_the_field_and_leaves_its_value_out_of_the_log(
    world: Any, caplog: pytest.LogCaptureFixture
):
    """The value is whatever the repository or extension put there, of any size: pydantic's own
    text repeats it, so the log gets the field and the rule it broke instead."""
    client = _Client(REFUSE)
    agent, session_id = await _open(world, client)
    broken = CommandDef(
        name="broken",
        description=["a text the repository wrote"],  # type: ignore[arg-type]
        handler=lambda args: None,
    )
    agent._harnesses[session_id].extension_registry.add_command(broken)
    agent._commands_advertised.discard(session_id)

    with caplog.at_level(logging.WARNING, logger="pi_agent_cli.agent"):
        await agent._advertise_commands(session_id)

    (message,) = [r.getMessage() for r in caplog.records if "broken" in r.getMessage()]
    assert "description" in message
    assert "a text the repository wrote" not in message


def test_a_failure_that_is_not_a_schema_error_is_reported_by_type_and_text():
    assert _why_invalid(RuntimeError("boom")) == "RuntimeError: boom"
