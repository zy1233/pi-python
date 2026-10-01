"""The ACP agent asks the user before it loads a project's own resources (audit P7-02 follow-up).

A project can ship extensions (imported, so run, as the session opens), prompt files and skills
(they decide what the model is told). Until now they were left out unless the user had listed
the project in ``agent.toml``, by path. Now, in an ACP client, the agent asks:

* only once the session exists, because a client drops anything sent for a session it has not
  been told about yet (so never during ``session/new``);
* only when there is something to decide, the user has not already said (in ``agent.toml``, on
  the command line, or by an earlier answer that still matches the files), and there is a
  client to ask;
* with a question that cannot be answered by an approve-everything mode: no ``allow_once``.

"Trust and remember" is saved with a fingerprint of the files, so a ``git pull`` that changes
one of them asks again. Nothing loads until the answer is in, and the first prompt waits for it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import text_block
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

from pi_agent_cli import agent as agent_module
from pi_agent_cli import extension_trust
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import TRUST_ENV, decide_project_trust
from pi_agent_cli.trust_prompt import REJECT_OPTION_ID, TRUST_OPTION_ID
from pi_agent_cli.trust_store import TrustStore
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import AgentHarness

TRUST = AllowedOutcome(option_id=TRUST_OPTION_ID, outcome="selected")
REFUSE = AllowedOutcome(option_id=REJECT_OPTION_ID, outcome="selected")
CANCELLED = DeniedOutcome(outcome="cancelled")

SHIPPED = (
    "from pathlib import Path\n"
    "Path({marker!r}).write_text('imported')\n"
    "def activate(pi):\n"
    "    pi.register_command('shipped', description='ext', handler=lambda args: None)\n"
)
SKILL = "---\nname: guide\ndescription: guide skill\n---\nBody\n"


@pytest.fixture(autouse=True)
def _quiet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An empty HOME, no installed extensions, and a project that ships an extension (whose
    *import* leaves a marker file behind) and a system prompt."""
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
    (extensions / "shipped.py").write_text(SHIPPED.format(marker=str(marker)), encoding="utf-8")
    (project / ".pi").mkdir()
    (project / ".pi" / "SYSTEM.md").write_text("Project system", encoding="utf-8")
    # Not the default home: code that forgets to pass the agent's home on would look in
    # ``home / ".pi-python"`` and find nothing, instead of the right place by coincidence.
    return SimpleNamespace(
        tmp=tmp_path, home=home, pi_home=tmp_path / "pi-home", project=project, marker=marker
    )


class _Client:
    """An ACP client: notes what it is sent, and answers the trust question as scripted."""

    def __init__(
        self,
        answer: Any = TRUST,
        *,
        hold: bool = False,
        fail: Exception | None = None,
        fail_first_update: Exception | None = None,
    ) -> None:
        self.updates: list[Any] = []
        self.requests: list[SimpleNamespace] = []
        # What reached the client, in order: ("update", <kind of update>) / ("request", None).
        self.events: list[tuple[str, str | None]] = []
        self.answer = answer
        self.fail = fail
        self.fail_first_update = fail_first_update
        self.asked = asyncio.Event()
        self.gate = asyncio.Event()
        if not hold:
            self.gate.set()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if self.fail_first_update is not None:
            failure, self.fail_first_update = self.fail_first_update, None
            raise failure
        self.updates.append(update)
        self.events.append(("update", getattr(update, "session_update", None)))

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        self.requests.append(
            SimpleNamespace(session_id=session_id, tool_call=tool_call, options=options)
        )
        self.events.append(("request", None))
        self.asked.set()
        await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return RequestPermissionResponse(outcome=self.answer)

    def messages(self) -> list[str]:
        return [
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]

    def text(self) -> str:
        return "".join(self.messages())

    def commands(self) -> set[str]:
        names: set[str] = set()
        for update in self.updates:
            if getattr(update, "session_update", None) == "available_commands_update":
                names.update(command.name for command in update.available_commands)
        return names


class _Llm:
    """A stream_fn that notes the system prompt of each turn."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        self.prompts.append(context.system_prompt)
        return await mock_text_stream(model, context, options)


def _agent(world: Any, client: _Client | None, config: CliConfig | None = None, llm: Any = None):
    agent = PiAcpAgent(
        stream_fn=llm or mock_text_stream,
        home=world.pi_home,
        config=config or CliConfig(permission="ask", provider="mock", model_id="mock"),
    )
    if client is not None:
        agent.on_connect(client)  # type: ignore[arg-type]
    return agent


async def _idle(agent: PiAcpAgent) -> None:
    """Wait for the agent's background work (the deferred setup after ``session/new``).

    A task cancelled because its session closed is fine; one that failed is not.
    """
    results = await asyncio.wait_for(
        asyncio.gather(*list(agent._background_tasks), return_exceptions=True), 10
    )
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            raise result


async def _open(
    world: Any, client: _Client | None, config: CliConfig | None = None, llm: Any = None
):
    agent = _agent(world, client, config, llm)
    created = await agent.new_session(cwd=str(world.project))
    return agent, created.session_id


def _stored(world: Any) -> Any:
    return TrustStore(world.pi_home).get(world.project)


# ---------------------------------------------------------------------------
# When the question is asked
# ---------------------------------------------------------------------------


async def test_the_question_waits_until_the_session_response_has_gone_out(world: Any):
    """Sent earlier, a client that has not registered the session yet would drop it."""
    client = _Client()
    agent = _agent(world, client)

    created = await agent.new_session(cwd=str(world.project))

    assert client.requests == []
    await _idle(agent)
    assert [r.session_id for r in client.requests] == [created.session_id]


async def test_it_is_asked_with_a_question_no_approve_all_mode_can_answer(world: Any):
    client = _Client()
    agent, _ = await _open(world, client)
    await _idle(agent)

    [request] = client.requests
    assert request.tool_call.title == "loading this project's extensions and prompt files"
    assert [o.kind for o in request.options] == ["reject_once", "allow_always"]
    assert request.tool_call.raw_input["project"] == str(world.project.resolve())


async def test_the_project_is_explained_in_a_message_just_before_the_question(world: Any):
    """The TUI draws a request's title and options and nothing of its content, so what the user
    needs to know to answer goes out first, as an ordinary message."""
    client = _Client(REFUSE)
    agent, _ = await _open(world, client)
    await _idle(agent)

    assert client.events[:2] == [("update", "agent_message_chunk"), ("request", None)]
    explanation = client.messages()[0]
    assert str(world.project.resolve()) in explanation
    assert "extensions: `.pi-python/extensions` (`shipped.py`)" in explanation
    assert "prompt: `.pi/SYSTEM.md`" in explanation
    assert "Python code" in explanation
    assert client.requests[0].tool_call.content is None  # said once, not twice


async def test_a_question_whose_explanation_cannot_be_delivered_is_not_asked(world: Any):
    """A yes or no about something the user was not told is worth nothing. (A client that cannot
    take a message is most likely a dead connection, too.)"""
    client = _Client(fail_first_update=RuntimeError("connection closed"))
    agent, session_id = await _open(world, client)
    await _idle(agent)

    assert client.requests == []
    assert not world.marker.exists()
    assert _stored(world) is None
    assert "could not be asked" in client.text()
    response = await agent.prompt(session_id=session_id, prompt=[text_block("hi")])
    assert response.stop_reason == "end_turn"  # the session itself is fine


async def test_the_setup_lets_the_session_new_response_out_before_it_sends_anything(world: Any):
    """Zed registers a session only after it has read the ``session/new`` response, and drops what
    it is sent for one it does not know yet. The setup is a task started before that response is
    written, so its first step must send nothing."""
    config = CliConfig(
        permission="ask", provider="mock", model_id="mock", trusted_projects=(str(world.project),)
    )
    client = _Client()
    agent = _agent(world, client, config)

    await agent.new_session(cwd=str(world.project))
    await asyncio.sleep(0)  # the setup takes its first step; the response goes out meanwhile

    assert client.events == []
    await _idle(agent)
    assert "shipped" in client.commands()  # and then it does send


async def test_asking_is_not_switched_off_by_auto_approval_of_tool_calls(world: Any):
    """``permission = "auto"`` is about tool calls, and asking is not approving."""
    for mode in ("auto", "always-approve"):
        client = _Client(REFUSE)
        agent, _ = await _open(world, client, CliConfig(permission=mode))  # type: ignore[arg-type]
        await _idle(agent)

        assert len(client.requests) == 1, mode


async def test_nothing_loads_until_the_answer_is_in(world: Any):
    client = _Client(hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)
    await asyncio.sleep(0.05)

    assert not world.marker.exists()  # the repository's code has not run
    assert "shipped" not in client.commands()
    assert agent._harnesses[session_id].extension_registry.get_commands() == {}

    client.gate.set()
    await _idle(agent)
    assert world.marker.exists()


async def test_a_project_that_asks_for_nothing_is_not_asked(world: Any, tmp_path: Path):
    bare = tmp_path / "bare"
    bare.mkdir()
    client = _Client()
    agent = _agent(world, client)

    await agent.new_session(cwd=str(bare))
    await _idle(agent)

    assert client.requests == []


async def test_it_is_not_asked_when_there_is_no_client(
    world: Any, caplog: pytest.LogCaptureFixture
):
    agent, session_id = await _open(world, None)
    await _idle(agent)

    harness = agent._harnesses[session_id]
    assert not world.marker.exists()
    assert [s.names for s in harness.skipped_extensions] == [("shipped.py",)]
    # Not asking is not a failure to ask.
    assert [r for r in caplog.records if r.name == agent_module.logger.name] == []


# ---------------------------------------------------------------------------
# Trust the user's configuration already gives: no question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config_of",
    [
        lambda world: CliConfig(trusted_projects=(str(world.project),)),
        lambda world: CliConfig(trust_project_extensions=True),
        lambda world: CliConfig(default_project_trust="always"),
    ],
    ids=["allow-list", "global switch", "default_project_trust = always"],
)
async def test_trusted_by_configuration_means_no_question(world: Any, config_of: Any):
    client = _Client()
    agent, _ = await _open(world, client, config_of(world))
    await _idle(agent)

    assert client.requests == []
    assert world.marker.exists()
    assert client.text() == ""


async def test_the_command_line_means_no_question(world: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(TRUST_ENV, "1")
    client = _Client()
    agent, _ = await _open(world, client)
    await _idle(agent)

    assert client.requests == []
    assert world.marker.exists()


async def test_never_means_no_question_and_says_why(world: Any):
    client = _Client()
    agent, _ = await _open(world, client, CliConfig(default_project_trust="never"))
    await _idle(agent)

    assert client.requests == []
    assert not world.marker.exists()
    assert "`default_project_trust` is set to `never`" in client.text()


async def test_content_that_cannot_be_pinned_is_not_asked_about(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    """An answer must be remembered against something; there is nothing to remember it by."""
    monkeypatch.setattr(extension_trust, "fingerprint_resources", lambda resources: None)
    client = _Client()
    agent, _ = await _open(world, client)
    await _idle(agent)

    assert client.requests == []
    assert not world.marker.exists()
    assert "too many, too large or unreadable" in client.text()
    assert "trusted_projects" in client.text()


# ---------------------------------------------------------------------------
# Saying yes
# ---------------------------------------------------------------------------


async def test_trusting_loads_the_extension_and_advertises_its_commands(world: Any):
    client = _Client(TRUST)
    agent, _ = await _open(world, client)
    await _idle(agent)

    assert world.marker.exists()
    assert "shipped" in client.commands()
    # Nothing was left out, so there is nothing to report: only the explanation went out.
    assert len(client.messages()) == 1


async def test_trusting_tells_the_model_what_the_project_says(world: Any):
    llm = _Llm()
    client = _Client(TRUST)
    agent, session_id = await _open(world, client, llm=llm)
    await _idle(agent)

    await agent.prompt(session_id=session_id, prompt=[text_block("hi")])

    assert "Project system" in llm.prompts[-1]


async def test_trusting_is_remembered_with_a_fingerprint_of_the_files(world: Any):
    client = _Client(TRUST)
    agent, _ = await _open(world, client)
    await _idle(agent)

    record = _stored(world)
    assert record is not None
    decision = decide_project_trust(CliConfig(), world.project, home=world.pi_home)
    assert record.fingerprint == decision.fingerprint
    assert any(".pi/SYSTEM.md" in item for item in record.resources)
    assert any("shipped.py" in item for item in record.resources)


async def test_a_remembered_answer_is_not_asked_again_by_a_later_agent(world: Any):
    first_client = _Client(TRUST)
    first, _ = await _open(world, first_client)
    await _idle(first)
    world.marker.unlink()

    second_client = _Client(REFUSE)  # would refuse, if asked
    second, _ = await _open(world, second_client)
    await _idle(second)

    assert second_client.requests == []
    assert world.marker.exists()
    assert second_client.text() == ""


async def test_a_remembered_answer_takes_effect_before_session_new_returns(world: Any):
    """Nothing is left to ask, so the session needs no deferred setup to settle it: the
    extension is imported, and its commands are there, as the session opens."""
    first, _ = await _open(world, _Client(TRUST))
    await _idle(first)
    world.marker.unlink()
    client = _Client(REFUSE)  # would refuse, if asked

    agent, session_id = await _open(world, client)  # the background setup has not run yet

    assert world.marker.exists()
    assert "shipped" in agent._harnesses[session_id].extension_registry.get_commands()
    await _idle(agent)
    assert client.requests == []


async def test_load_and_resume_ask_and_remember_like_new(world: Any):
    first, session_id = await _open(world, _Client(REFUSE))
    await _idle(first)

    loading = _Client(TRUST)
    second = _agent(world, loading)
    await second.load_session(cwd=str(world.project), session_id=session_id)
    await _idle(second)
    assert len(loading.requests) == 1
    assert world.marker.exists()

    world.marker.unlink()
    TrustStore(world.pi_home).forget(world.project)
    resuming = _Client(TRUST)
    third = _agent(world, resuming)
    await third.resume_session(session_id=session_id, cwd=str(world.project))
    await _idle(third)
    assert len(resuming.requests) == 1
    assert world.marker.exists()


async def test_the_agents_home_is_the_homes_of_its_sessions_extensions(world: Any, tmp_path: Path):
    """The trust store and the extension loader must look where the agent was told to."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "extensions").mkdir(parents=True)
    (elsewhere / "extensions" / "mine.py").write_text(
        "def activate(pi):\n"
        "    pi.register_command('mine', description='u', handler=lambda args: None)\n",
        encoding="utf-8",
    )
    client = _Client(REFUSE)
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=elsewhere,
        config=CliConfig(provider="mock", model_id="mock"),
    )
    agent.on_connect(client)  # type: ignore[arg-type]

    await agent.new_session(cwd=str(world.project))
    await _idle(agent)

    assert "mine" in client.commands()


# ---------------------------------------------------------------------------
# Saying no, and not saying
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(REFUSE, id="refused"),
        pytest.param(CANCELLED, id="cancelled"),
        pytest.param(
            AllowedOutcome(option_id="allow-once", outcome="selected"), id="a look-alike option"
        ),
        pytest.param(
            AllowedOutcome(option_id="something-else", outcome="selected"), id="an unknown option"
        ),
    ],
)
async def test_anything_but_the_trust_option_leaves_the_project_untrusted(world: Any, answer: Any):
    client = _Client(answer)
    agent, session_id = await _open(world, client)
    await _idle(agent)

    assert not world.marker.exists()
    assert "shipped" not in client.commands()
    assert _stored(world) is None
    assert "you chose not to trust this project" in client.text()
    assert "shipped.py" in client.text()
    assert "SYSTEM.md" in client.text()
    harness = agent._harnesses[session_id]
    assert [s.names for s in harness.skipped_extensions] == [("shipped.py",)]


async def test_a_refusal_is_not_remembered_so_the_next_session_asks_again(world: Any):
    first_client = _Client(REFUSE)
    first, _ = await _open(world, first_client)
    await _idle(first)

    second_client = _Client(TRUST)
    second, _ = await _open(world, second_client)
    await _idle(second)

    assert len(second_client.requests) == 1
    assert world.marker.exists()


async def test_the_model_is_not_told_what_an_untrusted_project_says(world: Any):
    llm = _Llm()
    agent, session_id = await _open(world, _Client(REFUSE), llm=llm)
    await _idle(agent)

    await agent.prompt(session_id=session_id, prompt=[text_block("hi")])

    assert "Project system" not in llm.prompts[-1]


async def test_a_client_that_fails_to_answer_leaves_the_project_untrusted(
    world: Any, caplog: pytest.LogCaptureFixture
):
    client = _Client(fail=RuntimeError("the client cannot show dialogs"))
    with caplog.at_level(logging.WARNING):
        agent, session_id = await _open(world, client)
        await _idle(agent)

    assert not world.marker.exists()
    assert _stored(world) is None
    assert "the client could not be asked" in client.text()
    # ... and the session works.
    response = await agent.prompt(session_id=session_id, prompt=[text_block("hi")])
    assert response.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# The files change: the answer no longer applies
# ---------------------------------------------------------------------------


async def test_a_changed_extension_asks_again_and_says_it_changed(world: Any):
    first_client = _Client(TRUST)
    first, _ = await _open(world, first_client)
    await _idle(first)
    extension = world.project / ".pi-python" / "extensions" / "shipped.py"
    extension.write_text(extension.read_text(encoding="utf-8") + "\n# pulled\n", encoding="utf-8")
    world.marker.unlink()

    second_client = _Client(REFUSE)
    second, _ = await _open(world, second_client)
    await _idle(second)

    [request] = second_client.requests
    assert request.tool_call.raw_input["changed"] is True
    assert "changed since you last trusted it" in second_client.text()
    assert not world.marker.exists()  # the new version did not run


async def test_an_added_extension_does_not_ride_on_the_old_answer(world: Any):
    first, _ = await _open(world, _Client(TRUST))
    await _idle(first)
    (world.project / ".pi-python" / "extensions" / "evil.py").write_text(
        "def activate(pi):\n    raise SystemExit('pwned')\n", encoding="utf-8"
    )

    client = _Client(REFUSE)
    second, _ = await _open(world, client)
    await _idle(second)

    assert len(client.requests) == 1


async def test_files_that_change_while_the_question_is_open_are_not_trusted(world: Any):
    """A dialog can stay open for minutes (a ``git pull`` in another window). What the user
    approved is what they were shown, not whatever is there when they click."""
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)
    extension = world.project / ".pi-python" / "extensions" / "shipped.py"
    extension.write_text(extension.read_text(encoding="utf-8") + "\n# swapped\n", encoding="utf-8")

    client.gate.set()
    await _idle(agent)

    assert not world.marker.exists()
    assert _stored(world) is None
    assert "changed while" in client.text()
    assert "shipped" not in client.commands()
    harness = agent._harnesses[session_id]
    assert [s.names for s in harness.skipped_extensions] == [("shipped.py",)]


# ---------------------------------------------------------------------------
# A saved answer that cannot be saved
# ---------------------------------------------------------------------------


async def test_when_the_answer_cannot_be_saved_it_still_holds_for_this_session(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    def broken(self: TrustStore, *args: Any, **kwargs: Any) -> Any:
        raise OSError("read-only file system")

    monkeypatch.setattr(TrustStore, "remember", broken)
    client = _Client(TRUST)
    agent, _ = await _open(world, client)
    await _idle(agent)

    assert world.marker.exists()  # the user said yes; this session honours it
    assert "could not be saved" in client.text()
    assert "read-only file system" in client.text()
    assert "asked again" in client.text()


# ---------------------------------------------------------------------------
# Around the question: prompt, cancel, close, concurrency
# ---------------------------------------------------------------------------


async def test_the_first_prompt_waits_for_the_answer(world: Any):
    llm = _Llm()
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client, llm=llm)
    await asyncio.wait_for(client.asked.wait(), 5)

    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("hi")]))
    await asyncio.sleep(0.1)
    assert not turn.done()
    assert llm.prompts == []  # no model call before the user has answered

    client.gate.set()
    response = await asyncio.wait_for(turn, 10)

    assert response.stop_reason == "end_turn"
    assert "Project system" in llm.prompts[-1]  # built after the answer, so it sees the file
    assert world.marker.exists()


async def test_a_refusal_lets_the_waiting_prompt_run_without_the_project(world: Any):
    llm = _Llm()
    client = _Client(REFUSE, hold=True)
    agent, session_id = await _open(world, client, llm=llm)
    await asyncio.wait_for(client.asked.wait(), 5)
    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("hi")]))
    await asyncio.sleep(0.05)

    client.gate.set()
    response = await asyncio.wait_for(turn, 10)

    assert response.stop_reason == "end_turn"
    assert "Project system" not in llm.prompts[-1]
    assert not world.marker.exists()


async def test_cancelling_while_the_question_is_open_ends_the_waiting_prompt(world: Any):
    llm = _Llm()
    client = _Client(REFUSE, hold=True)
    agent, session_id = await _open(world, client, llm=llm)
    await asyncio.wait_for(client.asked.wait(), 5)
    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("hi")]))
    await asyncio.sleep(0.05)

    await agent.cancel(session_id)
    response = await asyncio.wait_for(turn, 5)

    assert response.stop_reason == "cancelled"
    assert llm.prompts == []  # the user cancelled the turn, so it must not run afterwards
    client.gate.set()
    await _idle(agent)
    assert llm.prompts == []


async def test_a_prompt_after_a_cancelled_one_is_not_cancelled_too(world: Any):
    """A cancel ends the prompts that were waiting when it came, and no others: the next one, put
    while the question is still open, waits for the answer like the first did."""
    client = _Client(REFUSE, hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)
    one = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("one")]))
    await asyncio.sleep(0.05)
    await agent.cancel(session_id)
    assert (await asyncio.wait_for(one, 5)).stop_reason == "cancelled"

    two = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("two")]))
    await asyncio.sleep(0.05)
    assert not two.done()

    client.gate.set()
    assert (await asyncio.wait_for(two, 5)).stop_reason == "end_turn"


async def test_one_cancel_ends_every_prompt_waiting_for_the_answer(world: Any):
    llm = _Llm()
    client = _Client(REFUSE, hold=True)
    agent, session_id = await _open(world, client, llm=llm)
    await asyncio.wait_for(client.asked.wait(), 5)
    turns = [
        asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block(word)]))
        for word in ("a", "b")
    ]
    await asyncio.sleep(0.05)

    await agent.cancel(session_id)
    responses = await asyncio.wait_for(asyncio.gather(*turns), 5)

    assert [r.stop_reason for r in responses] == ["cancelled", "cancelled"]
    assert llm.prompts == []


async def test_a_cancel_with_no_question_open_is_an_ordinary_cancel(world: Any):
    agent, session_id = await _open(world, _Client(REFUSE))
    await _idle(agent)

    await agent.cancel(session_id)
    response = await agent.prompt(session_id=session_id, prompt=[text_block("hi")])

    assert response.stop_reason == "end_turn"  # a stale cancel does not poison the next turn


async def test_closing_the_session_abandons_the_question(world: Any):
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)

    await asyncio.wait_for(agent.close_session(session_id), 5)
    await _idle(agent)
    client.gate.set()
    await asyncio.sleep(0.05)

    assert not world.marker.exists()
    assert _stored(world) is None  # an answer to a session that no longer exists is nothing
    assert agent._background_tasks == set()


async def test_deleting_the_session_abandons_the_question(world: Any):
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)

    await asyncio.wait_for(agent.ext_method("pi/session/delete", {"sessionId": session_id}), 5)
    await _idle(agent)
    client.gate.set()
    await asyncio.sleep(0.05)

    assert not world.marker.exists()
    assert _stored(world) is None


async def test_a_prompt_waiting_on_a_closed_session_is_cancelled_like_any_other(world: Any):
    """ACP: closing a session cancels what it was doing."""
    llm = _Llm()
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client, llm=llm)
    await asyncio.wait_for(client.asked.wait(), 5)
    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("hi")]))
    await asyncio.sleep(0.05)

    await asyncio.wait_for(agent.close_session(session_id), 5)
    response = await asyncio.wait_for(turn, 5)

    assert response.stop_reason == "cancelled"
    assert llm.prompts == []


async def test_two_sessions_in_one_project_are_asked_once(world: Any):
    client = _Client(TRUST, hold=True)
    agent = _agent(world, client)
    first = await agent.new_session(cwd=str(world.project))
    second = await agent.new_session(cwd=str(world.project))
    await asyncio.wait_for(client.asked.wait(), 5)
    await asyncio.sleep(0.2)  # the second session reaches the question and has to wait

    client.gate.set()
    await _idle(agent)

    assert len(client.requests) == 1
    for session_id in (first.session_id, second.session_id):
        assert "shipped" in agent._harnesses[session_id].extension_registry.get_commands()


async def test_two_projects_are_asked_separately(world: Any, tmp_path: Path):
    other = tmp_path / "other"
    (other / ".pi").mkdir(parents=True)
    (other / ".pi" / "SYSTEM.md").write_text("Other system", encoding="utf-8")
    client = _Client(REFUSE)
    agent = _agent(world, client)

    await agent.new_session(cwd=str(world.project))
    await agent.new_session(cwd=str(other))
    await _idle(agent)

    assert len(client.requests) == 2
    assert {r.tool_call.raw_input["project"] for r in client.requests} == {
        str(world.project.resolve()),
        str(other.resolve()),
    }


@pytest.mark.skipif(os.name == "nt", reason="needs symlinks")
async def test_the_question_shows_where_the_project_really_is(world: Any, tmp_path: Path):
    """What the user is shown is where the answer is stored, not a path that only leads there."""
    link = tmp_path / "link"
    link.symlink_to(world.project, target_is_directory=True)
    client = _Client(REFUSE)
    agent = _agent(world, client)

    await agent.new_session(cwd=str(link))
    await _idle(agent)

    (request,) = client.requests
    assert Path(request.tool_call.raw_input["project"]) == world.project.resolve()
    assert str(world.project.resolve()) in client.messages()[0]  # the explanation says it too
    assert str(link) not in client.messages()[0]


# ---------------------------------------------------------------------------
# What else the answer decides, and the edges of the flow
# ---------------------------------------------------------------------------


def _with_a_skill(world: Any) -> CliConfig:
    """The project also ships a skill, and the configuration points at it."""
    skill = world.project / ".pi" / "skills" / "guide"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return CliConfig(
        permission="ask", provider="mock", model_id="mock", skills_dirs=(".pi/skills",)
    )


async def test_trusting_brings_the_projects_skills_too(world: Any):
    llm = _Llm()
    agent, session_id = await _open(world, _Client(TRUST), _with_a_skill(world), llm)

    await agent.prompt(session_id=session_id, prompt=[text_block("hi")])

    assert "<name>guide</name>" in llm.prompts[-1]


async def test_refusing_leaves_the_projects_skills_out(world: Any):
    llm = _Llm()
    agent, session_id = await _open(world, _Client(REFUSE), _with_a_skill(world), llm)

    await agent.prompt(session_id=session_id, prompt=[text_block("hi")])

    assert "<name>guide</name>" not in llm.prompts[-1]


async def test_a_remembered_answer_brings_the_skills_too(world: Any):
    config = _with_a_skill(world)
    first, _ = await _open(world, _Client(TRUST), config)
    await _idle(first)
    llm = _Llm()
    client = _Client(REFUSE)  # would say no, but is not asked

    agent, session_id = await _open(world, client, config, llm)
    await agent.prompt(session_id=session_id, prompt=[text_block("hi")])

    assert client.requests == []
    assert "<name>guide</name>" in llm.prompts[-1]


async def test_a_cancel_that_finds_nothing_waiting_is_not_kept_for_later(world: Any):
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)

    await agent.cancel(session_id)  # no prompt is waiting, so there is nothing for it to end
    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("hi")]))
    await asyncio.sleep(0.05)
    assert not turn.done()  # the prompt waits for the answer like any other

    client.gate.set()
    response = await asyncio.wait_for(turn, 5)
    assert response.stop_reason == "end_turn"


async def test_binding_a_session_again_withdraws_the_question_of_the_first_binding(world: Any):
    client = _Client(TRUST, hold=True)
    agent, session_id = await _open(world, client)
    await asyncio.wait_for(client.asked.wait(), 5)
    first = agent._harnesses[session_id]

    await agent.resume_session(session_id=session_id, cwd=str(world.project))
    client.gate.set()
    await _idle(agent)

    assert "shipped" not in first.extension_registry.get_commands()  # replaced: it gets nothing
    assert "shipped" in agent._harnesses[session_id].extension_registry.get_commands()


async def test_a_session_that_needs_no_question_looks_at_the_project_once(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    real = agent_module.decide_project_trust
    looked_at: list[str] = []

    def counting(config: CliConfig, cwd: str, **kwargs: Any) -> Any:
        looked_at.append(cwd)
        return real(config, cwd, **kwargs)

    monkeypatch.setattr(agent_module, "decide_project_trust", counting)
    config = CliConfig(
        permission="ask", provider="mock", model_id="mock", trusted_projects=(str(world.project),)
    )

    agent, _ = await _open(world, _Client(), config)
    await _idle(agent)

    assert len(looked_at) == 1  # hashing the project's files twice would be wasted work


async def test_the_project_is_looked_at_off_the_event_loop(
    world: Any, monkeypatch: pytest.MonkeyPatch
):
    """It reads and hashes files: done on the loop, it would stall the client's other traffic
    (and every other session's)."""
    real = agent_module.decide_project_trust
    loop_thread = threading.get_ident()
    looked_from: list[int] = []

    def noting(config: CliConfig, cwd: str, **kwargs: Any) -> Any:
        looked_from.append(threading.get_ident())
        return real(config, cwd, **kwargs)

    monkeypatch.setattr(agent_module, "decide_project_trust", noting)

    agent, _ = await _open(world, _Client(TRUST))
    await _idle(agent)

    assert looked_from  # at the start, before asking and after the dialog
    assert loop_thread not in looked_from


async def test_a_session_closed_as_its_setup_starts_does_not_strand_a_waiting_prompt(world: Any):
    """The prompt and the close arrive in the same breath as the session's own setup begins."""
    agent = _agent(world, _Client(TRUST))
    created = await agent.new_session(cwd=str(world.project))
    turn = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    )
    await asyncio.sleep(0)  # the setup and the prompt have each taken a step: the prompt waits

    await agent.close_session(created.session_id)
    response = await asyncio.wait_for(turn, 5)

    assert response.stop_reason == "cancelled"


async def test_a_failure_while_loading_extensions_does_not_strand_the_session(
    world: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    async def broken(self: AgentHarness) -> None:
        raise RuntimeError("loader blew up")

    monkeypatch.setattr(AgentHarness, "load_extensions", broken)
    agent, session_id = await _open(world, _Client(TRUST))  # asked, so it is not loaded at bind

    await _idle(agent)  # raises if the failure escaped the setup

    assert agent._trust[session_id].settled.is_set()  # a waiting prompt would be let go
    assert "loader blew up" in caplog.text
