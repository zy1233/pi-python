"""The permission policy: ask about every tool call unless the tool says it only reads.

``needs_permission`` used to be a list of tool names (``bash``, ``edit``, ``write``,
``workflow``), so a tool nobody had thought of -- any extension's -- ran without a prompt in
``ask`` mode. Now the answer comes from what the tool declares about itself (``annotations``,
MCP-style hints), and a tool that declares nothing is asked about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from acp import text_block
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
from pydantic import BaseModel

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.permissions import needs_permission
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ToolDefinition
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import AgentToolResult, DoneEvent, StartEvent

READS = {"readOnlyHint": True}
STAYS_INSIDE = {"destructiveHint": False, "openWorldHint": False}


# --- the policy itself ---------------------------------------------------------------


@pytest.mark.parametrize(
    "annotations",
    [
        READS,
        {"readOnlyHint": True, "openWorldHint": True},  # reads from the outside world: still reads
        {"readOnlyHint": True, "destructiveHint": True},  # contradicts itself: reads wins
        STAYS_INSIDE,
        {"readOnlyHint": False, **STAYS_INSIDE},
    ],
    ids=["read-only", "reads-the-web", "contradiction", "stays-inside", "writes-but-stays-inside"],
)
def test_a_tool_that_only_reads_or_changes_nothing_that_matters_is_not_asked_about(annotations):
    assert needs_permission("ask", annotations) is False


@pytest.mark.parametrize(
    "annotations",
    [
        None,
        {},
        {"readOnlyHint": False},
        {"readOnlyHint": "true"},
        {"readOnlyHint": 1},
        {"destructiveHint": False},  # MCP: an undeclared openWorldHint defaults to true
        {"openWorldHint": False},  # ... and an undeclared destructiveHint too
        {"destructiveHint": False, "openWorldHint": True},
        {"destructiveHint": True, "openWorldHint": False},
        {"destructiveHint": 0, "openWorldHint": 0},
        ["readOnlyHint"],
        "readOnlyHint",
    ],
    ids=[
        "nothing",
        "empty",
        "not-read-only",
        "string-true",
        "one",
        "only-non-destructive",
        "only-closed-world",
        "open-world",
        "destructive",
        "falsy-not-false",
        "a-list",
        "a-string",
    ],
)
def test_everything_else_is_asked_about(annotations):
    assert needs_permission("ask", annotations) is True


@pytest.mark.parametrize("mode", ["auto", "always-approve"])
@pytest.mark.parametrize("annotations", [None, {}, {"readOnlyHint": False}, READS])
def test_the_other_modes_never_ask(mode, annotations):
    assert needs_permission(mode, annotations) is False


# --- shipped extensions: no prompt that was not there before -------------------------


def _web_search() -> Any:
    return pytest.importorskip("pi_web_access.web_search").create_web_search_tool()


def _fetch_url() -> Any:
    return pytest.importorskip("pi_web_access.fetch_url").create_fetch_url_tool()


def _goal_update() -> Any:
    module = pytest.importorskip("pi_goal_x.goal_tool")
    return module.create_goal_update_tool(pytest.importorskip("pi_goal_x.goal_state").GoalState())


def _goal_complete() -> Any:
    module = pytest.importorskip("pi_goal_x.goal_tool")
    return module.create_goal_complete_tool(pytest.importorskip("pi_goal_x.goal_state").GoalState())


def _workflow() -> Any:
    return pytest.importorskip("pi_dynamic_workflows.workflow_tool").create_workflow_tool()


@pytest.mark.parametrize(
    ("make", "asks"),
    [
        pytest.param(_web_search, False, id="web_search"),
        pytest.param(_fetch_url, False, id="fetch_url"),
        pytest.param(_goal_update, False, id="goal_update"),
        pytest.param(_goal_complete, False, id="goal_complete"),
        pytest.param(_workflow, True, id="workflow"),
    ],
)
def test_the_shipped_extension_tools_ask_exactly_as_they_did_by_name(make, asks):
    """``web_search`` / ``fetch_url`` read, the goal tools only keep the agent's own notes in
    the session, and starting a workflow was always a decision for the user."""
    tool = make()

    assert needs_permission("ask", tool.annotations) is asks


# --- through the ACP agent ------------------------------------------------------------


class _Client:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.asked: list[str] = []
        self.updates: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.asked.append(tool_call.title.split()[0])
        if self.allow:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def finished(self) -> list[str]:
        """The final status of every tool call that was reported done."""
        return [
            update.status
            for update in self.updates
            if getattr(update, "session_update", None) == "tool_call_update"
            and update.status in {"completed", "failed"}
        ]


def _call_once(name: str, arguments: dict[str, Any]):
    """A model that calls tool *name* once and then says it is done."""

    async def stream(model, context, options=None):
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)
        events = AssistantMessageEventStream()
        call: ToolCallContent = {
            "type": "toolCall",
            "id": "call_1",
            "name": name,
            "arguments": arguments,
        }
        partial = _base_partial(model, [call])
        partial.stopReason = "toolUse"
        events.push(StartEvent(partial=partial.model_copy(deep=True)))
        events.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        events.set_final_message(partial)
        events.end()
        return events

    return stream


class _ProbeParams(BaseModel):
    message: str = ""


def _probe_extension(ran: list[str], annotations: dict[str, Any] | None):
    async def execute(tool_call_id, params, signal, on_update):
        ran.append(tool_call_id)
        return AgentToolResult(content=[{"type": "text", "text": "probed"}], details={})

    def extension(pi):
        pi.register_tool(
            ToolDefinition(
                name="probe",
                description="Probe something",
                parameters=_ProbeParams,
                execute=execute,
                annotations=annotations,
            )
        )

    return extension


def _agent(tmp_path: Path, stream_fn, *, extensions=(), permission: str = "ask") -> PiAcpAgent:
    return PiAcpAgent(
        stream_fn=stream_fn,
        home=tmp_path,
        config=CliConfig(permission=permission, provider="mock", model_id="mock"),  # type: ignore[arg-type]
        extensions=list(extensions),
    )


async def _run(agent: PiAcpAgent, client: _Client, tmp_path: Path) -> str:
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    await agent.prompt(session_id=created.session_id, prompt=[text_block("go")])
    return created.session_id


@pytest.mark.asyncio
async def test_in_ask_mode_a_read_runs_without_a_prompt(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("remember the milk\n", encoding="utf-8")
    client = _Client(allow=False)  # would refuse, if it were asked

    await _run(_agent(tmp_path, _call_once("read", {"path": str(note)})), client, tmp_path)

    assert client.asked == []
    assert client.finished() == ["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("annotations", [READS, STAYS_INSIDE], ids=["reads", "stays-inside"])
async def test_an_extension_tool_that_declares_itself_harmless_runs_without_a_prompt(
    tmp_path, annotations
):
    ran: list[str] = []
    client = _Client(allow=False)

    agent = _agent(
        tmp_path,
        _call_once("probe", {"message": "hi"}),
        extensions=[_probe_extension(ran, annotations)],
    )
    await _run(agent, client, tmp_path)

    assert client.asked == []
    assert ran == ["call_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "annotations",
    [None, {}, {"readOnlyHint": False}, {"destructiveHint": False}],
    ids=["nothing", "empty", "not-read-only", "only-non-destructive"],
)
async def test_an_extension_tool_that_declares_nothing_harmless_is_asked_about(
    tmp_path, annotations
):
    """Before, any tool that was not on the name list ran in ``ask`` mode unasked."""
    ran: list[str] = []
    client = _Client(allow=False)

    agent = _agent(
        tmp_path,
        _call_once("probe", {"message": "hi"}),
        extensions=[_probe_extension(ran, annotations)],
    )
    await _run(agent, client, tmp_path)

    assert client.asked == ["probe"]
    assert ran == []  # the user said no
    assert client.finished() == ["failed"]


@pytest.mark.asyncio
async def test_an_extension_tool_runs_once_the_user_allows_it(tmp_path):
    """Control for the refusal above: the tool really runs when the user agrees."""
    ran: list[str] = []
    client = _Client(allow=True)

    agent = _agent(
        tmp_path,
        _call_once("probe", {"message": "hi"}),
        extensions=[_probe_extension(ran, None)],
    )
    await _run(agent, client, tmp_path)

    assert client.asked == ["probe"]
    assert ran == ["call_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["auto", "always-approve"])
async def test_the_other_modes_still_run_everything_without_asking(tmp_path, permission):
    ran: list[str] = []
    client = _Client(allow=False)

    agent = _agent(
        tmp_path,
        _call_once("probe", {"message": "hi"}),
        extensions=[_probe_extension(ran, None)],
        permission=permission,
    )
    await _run(agent, client, tmp_path)

    assert client.asked == []
    assert ran == ["call_1"]


@pytest.mark.asyncio
async def test_a_subagent_call_is_judged_by_the_session_tool_of_that_name(tmp_path):
    """The workflow's sub-agents run on harnesses of their own; their calls go through the
    session's gate and are looked up in the session's tools by name. A name the session does
    not have is asked about: nothing vouches for it."""
    client = _Client(allow=True)
    agent = _agent(tmp_path, mock_text_stream)
    session_id = await _run(agent, client, tmp_path)
    harness = agent._harnesses[session_id]
    origin = {"kind": "subagent", "cwd": str(tmp_path)}

    for name in ("read", "grep", "find", "ls"):
        assert await harness.check_tool_call(f"subagent-1:{name}", name, {}, origin=origin) is None
    assert client.asked == []

    for name in ("bash", "edit", "write", "mystery"):
        await harness.check_tool_call(f"subagent-1:{name}", name, {}, origin=origin)
    assert client.asked == ["bash", "edit", "write", "mystery"]
