"""The Auto permission mode inside the ACP agent (PR 1b: the rules, wired in).

What is asserted here is what the red-team report asked of the wiring:

* W-1: an Auto session runs the calls of one assistant message one by one, in the order
  decide, run, decide, run (the rules judge a call on the files as they are at that moment).
* W-2: the facts that the rules cannot check themselves are checked when the session starts
  and before each call: a project folder that is there, folders that are absolute, a ``PATH``
  with no relative entry, a Bash tool that runs bash, tool objects of ours known by identity.
  While a check fails, every call that is not harmless asks, and the user is told.
* W-3: the default protected list is one constant; a write to each of its entries asks, and a
  write to ``Makefile``, ``package.json`` or ``conftest.py`` does not.
* W-5: a ``bash`` call that Auto let through has a time limit and an output limit; a call
  that the user allowed has none.

The rule layer itself is tested in ``test_auto_rules.py`` and ``test_auto_corpus.py``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import text_block
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
from pydantic import BaseModel

from pi_agent_cli import auto_mode
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.auto_mode import AUDIT_ENTRY_TYPE, LOG_COMMAND, AutoSession
from pi_agent_cli.auto_reasons import Reason, sentence
from pi_agent_cli.auto_rules import DEFAULT_PROTECTED_GLOBS, Outcome
from pi_agent_cli.config import AutoConfig, CliConfig
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ToolDefinition
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import AgentToolResult, DoneEvent, StartEvent

# --------------------------------------------------------------------------- helpers


class _World:
    """A home for the agent and a project that is another folder (Auto protects the home)."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home" / ".pi-python"
        self.project = root / "project"
        self.project.mkdir()
        self.home.mkdir(parents=True)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


class _Client:
    """The client: records what it is sent, and answers the questions with *allow*."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.updates: list[Any] = []
        self.requests: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.requests.append(tool_call)
        if self.allow:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def titles(self) -> list[str]:
        return [request.title for request in self.requests]

    def messages(self) -> list[str]:
        return [
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]


def _calls_stream(*calls: tuple[str, str, dict[str, Any]]):
    """A model that makes all of *calls* ``(id, tool, arguments)`` in ONE assistant message, and
    says it is done once their results are in."""

    async def stream(model, context, options=None):
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)
        events = AssistantMessageEventStream()
        content: list[ToolCallContent] = [
            {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}
            for call_id, name, arguments in calls
        ]
        partial = _base_partial(model, content)
        partial.stopReason = "toolUse"
        events.push(StartEvent(partial=partial.model_copy(deep=True)))
        events.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        events.set_final_message(partial)
        events.end()
        return events

    return stream


def _agent(
    world: _World,
    stream_fn: Any = mock_text_stream,
    *,
    permission: str = "auto",
    extensions: list[Any] | None = None,
    auto: AutoConfig | None = None,
    project_trust: str | None = None,
) -> PiAcpAgent:
    config = CliConfig(
        permission=permission,  # type: ignore[arg-type]
        provider="mock",
        model_id="mock",
        auto=auto or AutoConfig(classifier="rules"),
        default_project_trust=project_trust,  # type: ignore[arg-type]
    )
    return PiAcpAgent(
        stream_fn=stream_fn, home=world.home, config=config, extensions=extensions or []
    )


async def _open(
    world: _World,
    stream_fn: Any = mock_text_stream,
    *,
    client: _Client | None = None,
    cwd: Path | str | None = None,
    **kwargs: Any,
) -> tuple[PiAcpAgent, _Client, str]:
    agent = _agent(world, stream_fn, **kwargs)
    client = client or _Client()
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(cwd if cwd is not None else world.project))
    await _settled(agent)
    return agent, client, created.session_id


async def _settled(agent: PiAcpAgent) -> None:
    """The setup that follows ``session/new`` has run (the notices went out)."""
    await asyncio.gather(*agent._background_tasks)


async def _prompt(agent: PiAcpAgent, session_id: str, text: str = "go") -> None:
    await agent.prompt(session_id=session_id, prompt=[text_block(text)])


def _event(name: str, arguments: dict[str, Any], call_id: str = "c1", origin: Any = None) -> Any:
    """What the harness hands to the ``tool_call`` hook (``annotations`` come from the tool)."""
    return SimpleNamespace(
        toolName=name, toolCallId=call_id, input=arguments, annotations=None, origin=origin
    )


def _verdict_of(agent: PiAcpAgent, session_id: str):
    async def decide(name: str, arguments: dict[str, Any], call_id: str = "c1", origin=None):
        return await agent._autos[session_id].decide(_event(name, arguments, call_id, origin))

    return decide


# ---------------------------------------------------------------------------- W-1


async def test_auto_runs_the_calls_of_one_message_in_the_order_decide_run_decide_run(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    log: list[str] = []
    real_decide = auto_mode.decide_rules_only

    def spy_decide(call, ctx):
        log.append(f"decide:{call.name}")
        return real_decide(call, ctx)

    monkeypatch.setattr(auto_mode, "decide_rules_only", spy_decide)
    target = world.project / "a.txt"
    stream = _calls_stream(
        ("call_w", "write", {"path": str(target), "content": "one\n"}),
        ("call_b", "bash", {"command": "cat a.txt"}),
    )
    agent, client, session_id = await _open(world, stream)
    harness = agent._harnesses[session_id]
    for name in ("write", "bash"):
        _spy_on_run(harness.get_tool(name), name, log)

    await _prompt(agent, session_id)

    assert log == [
        "decide:write",
        "run:write:start",
        "run:write:end",
        "decide:bash",
        "run:bash:start",
        "run:bash:end",
    ]
    assert client.requests == []  # both were allowed by the rules: no question
    assert target.read_text(encoding="utf-8") == "one\n"


def _spy_on_run(tool: Any, label: str, log: list[str]) -> None:
    inner = tool.execute_fn

    async def wrapped(tool_call_id, params, signal=None, on_update=None):
        log.append(f"run:{label}:start")
        await asyncio.sleep(0.02)  # a call that takes a moment: a parallel batch would overlap
        try:
            return await inner(tool_call_id, params, signal, on_update)
        finally:
            log.append(f"run:{label}:end")

    tool.execute_fn = wrapped


@pytest.mark.parametrize("permission", ["ask", "always-approve"])
async def test_the_other_modes_still_run_the_calls_of_one_message_together(
    world: _World, permission: str
):
    """The control for the test above: it can tell one by one from together."""
    log: list[str] = []
    stream = _calls_stream(
        ("call_w", "write", {"path": str(world.project / "a.txt"), "content": "one\n"}),
        ("call_b", "bash", {"command": "cat a.txt"}),
    )
    agent, _, session_id = await _open(
        world, stream, client=_Client(allow=True), permission=permission
    )
    harness = agent._harnesses[session_id]
    for name in ("write", "bash"):
        _spy_on_run(harness.get_tool(name), name, log)

    await _prompt(agent, session_id)

    assert log[:2] == ["run:write:start", "run:bash:start"]  # both started before either ended


async def test_a_session_that_leaves_auto_runs_the_calls_together_again(world: _World):
    log: list[str] = []
    stream = _calls_stream(
        ("call_w", "write", {"path": str(world.project / "a.txt"), "content": "one\n"}),
        ("call_b", "bash", {"command": "cat a.txt"}),
    )
    agent, _, session_id = await _open(world, stream)
    harness = agent._harnesses[session_id]
    for name in ("write", "bash"):
        _spy_on_run(harness.get_tool(name), name, log)
    assert harness.tool_execution == "sequential"
    assert harness.get_tool("write").execution_mode == "sequential"

    await agent.set_config_option(config_id="mode", session_id=session_id, value="ask")

    assert harness.tool_execution == "parallel"
    assert harness.get_tool("write").execution_mode is None
    assert harness.get_tool("bash").execution_mode is None


async def test_a_session_that_starts_in_ask_mode_is_not_made_sequential(world: _World):
    agent, _, session_id = await _open(world, permission="ask")
    harness = agent._harnesses[session_id]

    assert harness.tool_execution == "parallel"
    assert harness.get_tool("write").execution_mode is None


class _FakeHarness:
    """The little of a harness that ``AutoSession`` uses (for the tests of its guards)."""

    def __init__(self, cwd: Path, tools: list[Any] | None = None) -> None:
        self.env = SimpleNamespace(cwd=str(cwd))
        self.tool_execution = "parallel"
        self.tools = {tool.name: tool for tool in tools or []}
        self.listeners: list[Callable[..., Any]] = []

    def get_tool(self, name: str) -> Any:
        return self.tools.get(name)

    def get_tools(self) -> list[Any]:
        return list(self.tools.values())

    def subscribe(self, listener: Callable[..., Any]) -> Callable[[], None]:
        self.listeners.append(listener)
        return lambda: None

    def emit(self, kind: str, call_id: str = "") -> None:
        for listener in self.listeners:
            listener(SimpleNamespace(type=kind, tool_call_id=call_id))


def _tool(name: str) -> Any:
    return SimpleNamespace(name=name, execution_mode=None)


def _fake_session(world: _World, tools: list[Any] | None = None, **kwargs: Any):
    harness = _FakeHarness(world.project, tools if tools is not None else [_tool("write")])
    config = CliConfig(permission="auto", auto=kwargs.pop("auto", AutoConfig(classifier="rules")))
    session = AutoSession(
        harness=harness,
        get_config=lambda: config,
        home=world.home,
        trusted=kwargs.pop("trusted", lambda: False),
        environ=kwargs.pop("environ", {"PATH": os.pathsep.join(["/usr/bin", "/bin"])}),
        resolve_shell=kwargs.pop("resolve_shell", lambda _path: SimpleNamespace(shell="/bin/bash")),
    )
    return harness, session


async def test_a_call_that_would_be_judged_before_an_earlier_call_of_its_message_has_run_asks(
    world: _World,
):
    """The guard behind W-1: Auto can be switched on while a batch is already being run
    together. The second call would then be judged on files that the first may still change."""
    harness, session = _fake_session(world)
    await session.enter()
    harness.emit("tool_execution_start", "c1")  # the first call has started and has not ended

    early = await session.decide(_event("write", {"path": "b.txt", "content": ""}, "c2"))

    assert early.outcome is Outcome.ASK
    assert early.rule_id == "session.parallel_batch"

    harness.emit("tool_execution_end", "c1")
    later = await session.decide(_event("write", {"path": "b.txt", "content": ""}, "c2"))
    assert later.outcome is Outcome.ALLOW


async def test_a_tool_that_is_added_after_auto_started_is_run_one_by_one_too(world: _World):
    harness, session = _fake_session(world)
    await session.enter()
    late = _tool("late_tool")
    harness.tools["late_tool"] = late
    assert late.execution_mode is None

    await session.decide(_event("late_tool", {}))

    assert late.execution_mode == "sequential"
    session.leave()
    assert late.execution_mode is None
    assert harness.tool_execution == "parallel"


# ---------------------------------------------------------------------------- W-2


async def test_a_project_folder_that_is_not_there_makes_every_call_ask_and_says_so(
    world: _World,
):
    gone = world.project / "gone"
    stream = _calls_stream(("c1", "bash", {"command": "cat a.txt"}))
    agent, client, session_id = await _open(world, stream, cwd=gone, client=_Client(allow=False))

    text = "\n".join(client.messages())
    assert "does not exist" in text  # the user sees the error when the session starts

    await _prompt(agent, session_id)

    assert [title.split()[0] for title in client.titles()] == ["bash"]
    assert "Auto:" in client.titles()[0]  # a call that Auto alone would have allowed


async def test_a_path_with_a_relative_entry_makes_every_call_ask_and_says_so(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "", "/bin"]))
    stream = _calls_stream(("c1", "bash", {"command": "cat a.txt"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert "PATH" in "\n".join(client.messages())
    assert len(client.requests) == 1  # asked, though the command is read-only


async def test_a_path_that_is_fixed_later_lets_the_calls_through_again(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    agent, _, session_id = await _open(world)
    decide = _verdict_of(agent, session_id)

    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", ".", "/bin"]))
    assert (await decide("bash", {"command": "ls"})).outcome is Outcome.ASK
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    assert (await decide("bash", {"command": "ls"})).outcome is Outcome.ALLOW


async def test_a_bash_tool_that_runs_another_shell_makes_every_call_ask(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        auto_mode, "get_shell_config", lambda path=None: SimpleNamespace(shell="C:/Windows/cmd.exe")
    )
    agent, client, session_id = await _open(world)
    decide = _verdict_of(agent, session_id)

    verdict = await decide("bash", {"command": "ls"})

    assert verdict.outcome is Outcome.ASK
    assert verdict.rule_id == "session.check_failed"
    assert "not bash" in "\n".join(client.messages())


async def test_a_bash_tool_that_cannot_find_a_shell_makes_every_call_ask(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    def no_shell(path=None):
        raise OSError("no shell")

    monkeypatch.setattr(auto_mode, "get_shell_config", no_shell)
    agent, _, session_id = await _open(world)

    verdict = await _verdict_of(agent, session_id)("bash", {"command": "ls"})

    assert verdict.outcome is Outcome.ASK


async def test_a_check_of_the_session_that_fails_makes_every_call_ask_and_says_so(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    """A check that cannot run is not a check that found nothing."""

    def broken(context):
        raise RuntimeError("boom")

    monkeypatch.setattr(auto_mode, "context_problems", broken)
    agent, client, session_id = await _open(world)

    verdict = await _verdict_of(agent, session_id)("write", {"path": "a.txt", "content": ""})

    assert verdict.outcome is Outcome.ASK
    assert verdict.rule_id == "session.check_failed"
    assert "could not read the state of the session" in "\n".join(client.messages())


async def test_a_check_of_the_session_that_takes_too_long_makes_every_call_ask(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    import time

    monkeypatch.setattr(auto_mode, "DECISION_TIMEOUT_SECONDS", 0.2)

    def slow(context):
        time.sleep(0.6)
        return []

    monkeypatch.setattr(auto_mode, "context_problems", slow)
    agent, client, session_id = await _open(world)
    monkeypatch.setattr(auto_mode, "DECISION_TIMEOUT_SECONDS", 20.0)  # the check is over

    verdict = await _verdict_of(agent, session_id)("write", {"path": "a.txt", "content": ""})

    assert verdict.outcome is Outcome.ASK
    assert verdict.rule_id == "session.check_failed"
    assert "could not read the state of the session" in "\n".join(client.messages())


@pytest.mark.parametrize("name", ["bash", "write", "edit"])
async def test_a_tool_of_an_extension_with_the_name_of_ours_is_not_ours(world: _World, name: str):
    """The rules know ``bash``, ``write`` and ``edit`` by what they do. A tool that an
    extension registers under one of those names does something else, so its calls ask."""
    ran: list[str] = []

    class Params(BaseModel):
        command: str = ""
        path: str = ""
        content: str = ""

    async def execute(tool_call_id, params, signal, on_update):
        ran.append(tool_call_id)
        return AgentToolResult(content=[{"type": "text", "text": "mine"}], details={})

    def extension(pi):
        pi.register_tool(
            ToolDefinition(name=name, description="Not ours", parameters=Params, execute=execute)
        )

    agent, _, session_id = await _open(world, extensions=[extension])
    await agent._harnesses[session_id].load_extensions()
    decide = _verdict_of(agent, session_id)
    arguments = {"command": "ls", "path": str(world.project / "x.txt"), "content": ""}

    verdict = await decide(name, arguments)

    assert verdict.outcome is Outcome.ASK
    # ... where the tool of ours with the same arguments is allowed (the control).
    plain, _, plain_id = await _open(world)
    assert (await _verdict_of(plain, plain_id)(name, arguments)).outcome is Outcome.ALLOW


async def test_a_folder_in_extra_roots_that_is_not_absolute_makes_every_call_ask(world: _World):
    agent, client, session_id = await _open(
        world, auto=AutoConfig(classifier="rules", extra_roots=("relative/dir",))
    )

    verdict = await _verdict_of(agent, session_id)("bash", {"command": "ls"})

    assert verdict.outcome is Outcome.ASK
    assert "relative/dir" in "\n".join(client.messages())


async def test_a_value_of_auto_that_cannot_be_used_makes_every_call_ask(world: _World):
    auto = AutoConfig(
        classifier="rules", problems=("An entry of protected in [auto] is not text.",)
    )
    agent, client, session_id = await _open(world, auto=auto)

    verdict = await _verdict_of(agent, session_id)("bash", {"command": "ls"})

    assert verdict.outcome is Outcome.ASK
    assert "protected in [auto]" in "\n".join(client.messages())


@pytest.mark.parametrize("permission", ["ask", "always-approve"])
async def test_a_value_of_auto_that_cannot_be_used_is_told_when_the_session_is_not_in_auto(
    world: _World, permission: str
):
    """The user sees it when the config loads, not first when Auto is turned on."""
    auto = AutoConfig(
        classifier="rules", problems=("An entry of protected in [auto] is not text.",)
    )
    _, client, _ = await _open(world, permission=permission, auto=auto)

    (text,) = client.messages()
    assert "protected in [auto]" in text
    assert "Auto mode is on" not in text


async def test_a_value_of_auto_that_cannot_be_used_is_told_once_when_the_session_is_in_auto(
    world: _World,
):
    auto = AutoConfig(
        classifier="rules", problems=("An entry of protected in [auto] is not text.",)
    )
    _, client, _ = await _open(world, auto=auto)

    (text,) = client.messages()
    assert text.startswith("Auto mode is on.")
    assert text.count("protected in [auto]") == 1


async def test_a_session_not_in_auto_with_good_settings_says_nothing_about_auto(world: _World):
    _, client, _ = await _open(world, permission="ask")

    assert client.messages() == []


async def test_a_session_with_nothing_wrong_says_only_what_auto_is(world: _World):
    _, client, _ = await _open(world)

    (text,) = client.messages()
    assert text.startswith("Auto mode is on.")
    assert "cannot check" not in text


async def test_a_failed_check_does_not_make_auto_ask_more_than_ask_mode_does(world: _World):
    """A tool that declares itself harmless is never asked about (rule A0), checks or not."""
    note = world.project / "note.txt"
    note.write_text("hello\n", encoding="utf-8")
    auto = AutoConfig(classifier="rules", extra_roots=("relative/dir",))
    agent, client, session_id = await _open(
        world,
        _calls_stream(("c1", "read", {"path": str(note)})),
        auto=auto,
        client=_Client(allow=False),
    )
    assert any("cannot check" in text for text in client.messages())  # the session is in the state

    await _prompt(agent, session_id)

    assert client.requests == []


# ---------------------------------------------------------------------------- W-3


_DEFAULT_PROTECTED_PATHS = [
    ".husky/pre-commit",
    ".githooks/pre-push",
    ".claude/settings.json",
    ".cursor/rules/style.mdc",
    ".mcp.json",
    ".vscode/tasks.json",
    ".github/workflows/ci.yml",
]


def test_the_default_protected_list_is_the_seven_entries_of_the_decision():
    """D-01. One constant: to change the list, change this and the test."""
    assert DEFAULT_PROTECTED_GLOBS == (
        ".husky/*",
        ".githooks/*",
        ".claude/*",
        ".cursor/*",
        ".mcp.json",
        ".vscode/tasks.json",
        ".github/workflows/*",
    )


@pytest.mark.parametrize("relative", _DEFAULT_PROTECTED_PATHS)
async def test_a_write_to_a_file_of_the_default_protected_list_asks(world: _World, relative: str):
    target = world.project / relative
    stream = _calls_stream(("c1", "write", {"path": str(target), "content": "x\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert [title.split()[0] for title in client.titles()] == ["write"]
    assert sentence(Reason.PROTECTED_PATH) in client.titles()[0]
    assert not target.exists()  # the user said no


@pytest.mark.parametrize("relative", ["Makefile", "package.json", "conftest.py"])
async def test_a_write_to_makefile_package_json_or_conftest_does_not_ask(
    world: _World, relative: str
):
    target = world.project / relative
    stream = _calls_stream(("c1", "write", {"path": str(target), "content": "x\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert client.requests == []
    assert target.read_text(encoding="utf-8") == "x\n"


async def test_the_users_own_protected_globs_come_on_top_of_the_default_list(world: _World):
    auto = AutoConfig(classifier="rules", protected=("secrets/*",))
    agent, _, session_id = await _open(world, auto=auto)
    decide = _verdict_of(agent, session_id)

    mine = await decide("write", {"path": str(world.project / "secrets" / "key"), "content": ""})
    default = await decide("write", {"path": str(world.project / ".mcp.json"), "content": ""})
    plain = await decide("write", {"path": str(world.project / "notes.txt"), "content": ""})

    assert (mine.outcome, default.outcome, plain.outcome) == (
        Outcome.ASK,
        Outcome.ASK,
        Outcome.ALLOW,
    )


# --------------------------------------------------------------------- what Auto asks


async def test_a_write_inside_the_project_runs_with_no_question(world: _World):
    target = world.project / "src" / "a.txt"
    stream = _calls_stream(("c1", "write", {"path": str(target), "content": "hi\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert client.requests == []
    assert target.read_text(encoding="utf-8") == "hi\n"


async def test_a_write_outside_the_project_asks_and_the_title_says_why(world: _World, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    stream = _calls_stream(("c1", "write", {"path": str(outside), "content": "hi\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=True))

    await _prompt(agent, session_id)

    (title,) = client.titles()
    assert title == f"write (Auto: {sentence(Reason.OUTSIDE_PROJECT)})"
    assert outside.read_text(encoding="utf-8") == "hi\n"  # the user said yes


async def test_a_bash_call_that_the_rules_cannot_prove_asks_the_user(world: _World):
    stream = _calls_stream(("c1", "bash", {"command": "python3 -c 'print(1)'"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    (title,) = client.titles()
    assert title.startswith("bash (Auto: ")
    # No command text, and no file name, goes into the title.
    assert "python3" not in title


async def test_a_refused_call_does_not_run(world: _World):
    marker = world.project / "marker.txt"
    stream = _calls_stream(("c1", "bash", {"command": f"touch {marker}"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert len(client.requests) == 1
    assert not marker.exists()


async def test_the_workflow_tool_asks_in_auto(world: _World, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("pi_dynamic_workflows")
    monkeypatch.setenv("HOME", str(world.project.parent))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: world.project.parent))
    script = 'meta = {"name": "none"}\nasync def main():\n    result("done")\n'
    stream = _calls_stream(("c1", "workflow", {"script": script, "args": {}}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert [title.split()[0] for title in client.titles()] == ["workflow"]


async def test_auto_runs_a_call_the_user_would_not_be_asked_about_in_ask_mode(world: _World):
    note = world.project / "note.txt"
    note.write_text("hello\n", encoding="utf-8")
    stream = _calls_stream(("c1", "read", {"path": str(note)}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert client.requests == []


async def test_with_no_client_to_ask_a_call_that_asks_is_blocked(world: _World):
    marker = world.project / "marker.txt"
    stream = _calls_stream(("c1", "bash", {"command": f"touch {marker}"}))
    agent = _agent(world, stream)
    created = await agent.new_session(cwd=str(world.project))  # no client connected

    await _prompt(agent, created.session_id)

    assert not marker.exists()


# --------------------------------------------------------- the trust of the project (invariant 9)


async def test_code_of_a_project_that_nobody_vouched_for_asks_and_says_why(world: _World):
    """What the project brings along runs only after the user vouched for it. The trust of the
    session goes to the rules as it is."""
    agent, _, session_id = await _open(world)

    verdict = await _verdict_of(agent, session_id)("bash", {"command": "pytest"})

    assert verdict.outcome is Outcome.ASK
    assert verdict.reason is Reason.UNTRUSTED_PROJECT_CODE
    assert verdict.rule_id == "bash.untrusted_project_code"


async def test_code_of_a_project_that_the_user_vouched_for_is_left_to_the_next_step(
    world: _World,
):
    agent, _, session_id = await _open(world, project_trust="always")

    verdict = await _verdict_of(agent, session_id)("bash", {"command": "pytest"})

    # The rules cannot prove it harmless, and the model step is not built yet: it asks, but
    # not for the reason of the first test.
    assert verdict.outcome is Outcome.ASK
    assert verdict.reason is Reason.UNCLEAR
    assert verdict.rule_id == "pipeline.unclear"


async def test_the_trust_of_the_project_is_read_again_for_each_call(world: _World):
    """The user can vouch for the project while the session runs (the question at the start)."""
    trusted = [False]
    _, session = _fake_session(world, [_tool("bash")], trusted=lambda: trusted[0])
    await session.enter()

    before = await session.decide(_event("bash", {"command": "pytest"}, "c1"))
    trusted[0] = True
    after = await session.decide(_event("bash", {"command": "pytest"}, "c2"))

    assert before.rule_id == "bash.untrusted_project_code"
    assert after.rule_id == "pipeline.unclear"


# ------------------------------------------------------- one call at a time, shown on a link (D-03)


class _AnswersByTool(_Client):
    """A client that says yes to the tools in *allow* and no to the rest."""

    def __init__(self, allow: set[str]) -> None:
        super().__init__(allow=False)
        self.allowed_tools = allow

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.allow = tool_call.title.split()[0] in self.allowed_tools
        return await super().request_permission(session_id, tool_call, options, **kwargs)


needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="ln -s needs a POSIX shell")


def _link_then_write(project: Path, outside: Path) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    """Two calls of ONE message: a link in the project that points out of it, then a write
    through the link. Judged before the link exists, the write looks like an edit in the project."""
    return (
        ("c1", "bash", {"command": f"ln -s {outside} link"}),
        ("c2", "write", {"path": str(project / "link" / "escape.txt"), "content": "out\n"}),
    )


@needs_symlinks
async def test_a_write_through_a_link_that_an_earlier_call_made_is_judged_after_the_link_exists(
    world: _World, tmp_path: Path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    stream = _calls_stream(*_link_then_write(world.project, outside))
    client = _AnswersByTool({"bash"})  # yes to the link, no to the write
    agent, _, session_id = await _open(world, stream, client=client)

    await _prompt(agent, session_id)

    assert [title.split()[0] for title in client.titles()] == ["bash", "write"]
    # The write was judged on the link as it was by then: a path that leaves the project. (A
    # question that says "cannot tell" would come from the guard against a parallel batch.)
    assert client.titles()[1] == f"write (Auto: {sentence(Reason.OUTSIDE_PROJECT)})"
    assert (world.project / "link").is_symlink()  # the user allowed the first call: it ran
    assert not (outside / "escape.txt").exists()  # the second was asked about, and refused


@needs_symlinks
async def test_the_same_two_calls_in_ask_mode_are_both_put_to_the_user(
    world: _World, tmp_path: Path
):
    """The control: with no Auto the user is asked about both, whatever order they run in."""
    outside = tmp_path / "outside"
    outside.mkdir()
    stream = _calls_stream(*_link_then_write(world.project, outside))
    client = _AnswersByTool({"bash"})
    agent, _, session_id = await _open(world, stream, client=client, permission="ask")

    await _prompt(agent, session_id)

    assert [title.split()[0] for title in client.titles()] == ["bash", "write"]
    assert not (outside / "escape.txt").exists()


class _LinkInASubagent:
    """A model for a session that runs one workflow, and for the sub-agent of that workflow.

    The session calls ``workflow`` once. The sub-agent makes the two calls of ``_link_then_write``
    in ONE message. Which of the two the model is, is told by the tools it is given.
    """

    SCRIPT = (
        'meta = {"name": "link"}\nasync def main():\n    await agent("go")\n    result("done")\n'
    )

    def __init__(self, project: Path, outside: Path) -> None:
        self._subagent = _calls_stream(*_link_then_write(project, outside))

    async def stream(self, model, context, options=None):
        if "workflow" in {tool.name for tool in context.tools or []}:
            if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
                return await mock_text_stream(model, context, options)
            return await _calls_stream(("w1", "workflow", {"script": self.SCRIPT, "args": {}}))(
                model, context, options
            )
        return await self._subagent(model, context, options)


@needs_symlinks
async def test_a_subagent_write_through_a_link_that_its_own_earlier_call_made_asks(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("pi_dynamic_workflows")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    model = _LinkInASubagent(world.project, outside)
    client = _AnswersByTool({"workflow", "bash"})
    agent, _, session_id = await _open(world, model.stream, client=client)

    await _prompt(agent, session_id)

    assert [title.split()[0] for title in client.titles()] == ["workflow", "bash", "write"]
    assert "sub-agent" in client.titles()[2]
    assert sentence(Reason.OUTSIDE_PROJECT) in client.titles()[2]
    assert (world.project / "link").is_symlink()
    assert not (outside / "escape.txt").exists()


# -------------------------------------------------------------------- sub-agents (D-03)


async def test_a_subagent_call_is_judged_in_the_folder_of_the_subagent(world: _World):
    agent, _, session_id = await _open(world)
    decide = _verdict_of(agent, session_id)
    inner = world.project / "inner"
    inner.mkdir()
    origin = {"kind": "subagent", "cwd": str(inner), "label": "x"}

    verdict = await decide("write", {"path": "notes.txt", "content": ""}, origin=origin)

    assert verdict.outcome is Outcome.ALLOW  # ``notes.txt`` is ``inner/notes.txt``: in the project


async def test_a_subagent_that_works_outside_the_project_asks(world: _World, tmp_path: Path):
    agent, _, session_id = await _open(world)
    other = tmp_path / "other"
    other.mkdir()
    origin = {"kind": "subagent", "cwd": str(other), "label": "x"}

    verdict = await _verdict_of(agent, session_id)(
        "write", {"path": str(other / "a.txt"), "content": ""}, origin=origin
    )

    assert verdict.outcome is Outcome.ASK
    assert verdict.reason is Reason.OUTSIDE_PROJECT


@pytest.mark.parametrize(
    "origin",
    [
        {"kind": "robot"},
        {"kind": "robot", "cwd": "PROJECT"},
        {"cwd": "PROJECT"},
        {"kind": ["subagent"], "cwd": "PROJECT"},
        "subagent",
        ["subagent"],
        1,
    ],
    ids=["other-kind", "other-kind-with-folder", "no-kind", "kind-not-text", "text", "list", "int"],
)
async def test_a_call_with_an_origin_that_is_not_a_subagent_asks(world: _World, origin: Any):
    """An origin that Auto does not know is not judged as the session's own call, nor as a
    sub-agent's: the folder that it names (if any) is not trusted."""
    agent, _, session_id = await _open(world)
    if isinstance(origin, dict) and origin.get("cwd") == "PROJECT":
        origin = {**origin, "cwd": str(world.project)}

    verdict = await _verdict_of(agent, session_id)(
        "write", {"path": str(world.project / "a.txt"), "content": ""}, origin=origin
    )

    assert verdict.outcome is Outcome.ASK
    assert verdict.rule_id == "session.unknown_origin"


@pytest.mark.parametrize("folder", [None, "", 1, ["x"]], ids=["missing", "empty", "int", "list"])
async def test_a_subagent_without_a_folder_asks(world: _World, folder: Any):
    agent, _, session_id = await _open(world)
    origin: dict[str, Any] = {"kind": "subagent"}
    if folder is not None:
        origin["cwd"] = folder

    verdict = await _verdict_of(agent, session_id)(
        "write", {"path": str(world.project / "a.txt"), "content": ""}, origin=origin
    )

    assert verdict.outcome is Outcome.ASK
    assert verdict.rule_id == "session.subagent_without_cwd"


# ------------------------------------------------------------------------- the audit


async def test_every_decision_of_auto_is_recorded_without_the_command_text(world: _World):
    marker = world.project / "marker.txt"
    secret_command = f'python3 -c \'open("{marker}", "w")\'  # hunter2'
    stream = _calls_stream(
        ("c1", "write", {"path": str(world.project / "a.txt"), "content": "SECRET-CONTENT\n"}),
        ("c2", "bash", {"command": secret_command}),
    )
    agent, _, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    records = await _records(agent._harnesses[session_id])
    assert [(r["tool"], r["outcome"]) for r in records] == [("write", "allow"), ("bash", "ask")]
    assert records[0]["by"] == "rule"
    assert records[1]["by"] == "floor"
    assert records[1]["classifier"] == "rules"
    assert {record["tool_call_id"] for record in records} == {"c1", "c2"}
    # What is saved names the decision and the rule, never what the call said.
    saved = repr(records) + (await _session_file_text(agent._harnesses[session_id]))
    assert AUDIT_ENTRY_TYPE in saved
    for secret in ("hunter2", "python3 -c", str(marker)):
        assert secret not in repr(records)


async def _records(harness: Any) -> list[dict[str, Any]]:
    entries = await harness.session.get_entries()
    return [
        entry.data for entry in entries if getattr(entry, "customType", None) == AUDIT_ENTRY_TYPE
    ]


async def _session_file_text(harness: Any) -> str:
    metadata = await harness.session.get_metadata()
    path = getattr(metadata, "path", None)
    return Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else ""


async def test_a_call_that_declares_itself_harmless_leaves_no_record(world: _World):
    note = world.project / "note.txt"
    note.write_text("hello\n", encoding="utf-8")
    stream = _calls_stream(("c1", "read", {"path": str(note)}))
    agent, _, session_id = await _open(world, stream)

    await _prompt(agent, session_id)

    assert await _records(agent._harnesses[session_id]) == []


async def test_the_auto_log_command_says_what_auto_decided(world: _World):
    stream = _calls_stream(
        ("c1", "write", {"path": str(world.project / "a.txt"), "content": "x\n"}),
        ("c2", "bash", {"command": "python3 -c 'print(1)'"}),
    )
    agent, _, session_id = await _open(world, stream, client=_Client(allow=False))
    await _prompt(agent, session_id)

    answer = await agent._harnesses[session_id].dispatch_command(LOG_COMMAND, "")

    assert answer is not None
    assert "2" in answer.splitlines()[0]
    assert "allowed 1, asked 1" in answer
    assert "write" in answer
    assert "bash" in answer


async def test_the_auto_log_command_with_no_decisions_says_so(world: _World):
    agent, _, session_id = await _open(world)

    answer = await agent._harnesses[session_id].dispatch_command(LOG_COMMAND, "")

    assert answer == "Auto has made no decisions in this session."


# ---------------------------------------------------------------- failing closed (9b)


async def test_an_error_in_the_decision_is_a_question_not_an_allow(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    def broken(call, ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(auto_mode, "decide_rules_only", broken)
    marker = world.project / "marker.txt"
    stream = _calls_stream(("c1", "write", {"path": str(marker), "content": "x\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    (title,) = client.titles()
    assert sentence(Reason.INTERNAL_ERROR) in title
    assert not marker.exists()


async def test_a_decision_that_takes_too_long_is_a_question_not_an_allow(
    world: _World, monkeypatch: pytest.MonkeyPatch
):
    import time

    monkeypatch.setattr(auto_mode, "DECISION_TIMEOUT_SECONDS", 0.2)
    real = auto_mode.decide_rules_only

    def slow(call, ctx):
        time.sleep(0.6)
        return real(call, ctx)

    monkeypatch.setattr(auto_mode, "decide_rules_only", slow)
    agent, _, session_id = await _open(world)

    verdict = await _verdict_of(agent, session_id)("write", {"path": "a.txt", "content": ""})

    assert verdict.outcome is Outcome.ASK
    assert verdict.reason is Reason.CHECKER_UNAVAILABLE


async def test_a_session_without_auto_state_asks_when_it_is_in_auto(world: _World):
    marker = world.project / "marker.txt"
    stream = _calls_stream(("c1", "write", {"path": str(marker), "content": "x\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))
    del agent._autos[session_id]

    await _prompt(agent, session_id)

    assert len(client.requests) == 1
    assert not marker.exists()


# ---------------------------------------------------------------- limits of a bash call (W-5)


async def test_a_bash_call_that_auto_allowed_stops_at_the_time_limit(world: _World):
    stream = _calls_stream(("c1", "bash", {"command": "sleep 30"}))
    auto = AutoConfig(classifier="rules", bash_timeout_seconds=0.5)
    agent, client, session_id = await _open(world, stream, auto=auto, client=_Client(allow=False))
    results: list[Any] = []
    agent._harnesses[session_id].subscribe(_collect_results(results))

    started = asyncio.get_running_loop().time()
    await _prompt(agent, session_id)

    assert asyncio.get_running_loop().time() - started < 15
    assert client.requests == []  # sleep is read-only: Auto allowed it
    (result,) = results
    assert result.isError is True
    assert "timed out" in result.content[0]["text"]


async def test_a_bash_call_that_auto_allowed_stops_at_the_output_limit(world: _World):
    stream = _calls_stream(("c1", "bash", {"command": "seq 1 100000000"}))
    auto = AutoConfig(classifier="rules", bash_max_output_mb=1)
    agent, _, session_id = await _open(world, stream, auto=auto, client=_Client(allow=False))
    results: list[Any] = []
    agent._harnesses[session_id].subscribe(_collect_results(results))

    await _prompt(agent, session_id)

    (result,) = results
    assert result.isError is True
    assert "limit" in result.content[0]["text"]


async def test_a_bash_call_that_the_user_allowed_has_no_limit_of_auto(world: _World):
    marker = world.project / "marker.txt"
    # Auto asks (the command writes), the user says yes: the user chose it, no limit applies.
    command = f"sleep 1.2; touch {marker}"
    stream = _calls_stream(("c1", "bash", {"command": command}))
    auto = AutoConfig(classifier="rules", bash_timeout_seconds=0.3)
    agent, client, session_id = await _open(world, stream, auto=auto, client=_Client(allow=True))

    await _prompt(agent, session_id)

    assert len(client.requests) == 1
    assert marker.exists()


def _collect_results(results: list[Any]):
    def listener(event: Any, signal: Any = None) -> None:
        if (
            getattr(event, "type", None) == "message_end"
            and getattr(event.message, "role", None) == "toolResult"
        ):
            results.append(event.message)

    return listener
