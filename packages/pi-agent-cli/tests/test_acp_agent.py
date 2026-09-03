"""ACP agent: standard methods, event projection, permission hook, no vendor RPCs."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError, text_block
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.events import project_event, tool_kind
from pi_agent_cli.permissions import PERMISSION_TOOLS, needs_permission
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, StartEvent


class FakeClient:
    def __init__(self, *, allow: bool = True) -> None:
        self.updates: list[tuple[str, Any]] = []
        self.permission_calls: list[dict[str, Any]] = []
        self.allow = allow

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update))

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.permission_calls.append(
            {
                "session_id": session_id,
                "tool_call": tool_call,
                "options": options,
            }
        )
        if self.allow:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def _agent(tmp_path: Path, stream_fn=mock_text_stream, permission: str = "ask") -> PiAcpAgent:
    return PiAcpAgent(
        stream_fn=stream_fn,
        home=tmp_path,
        config=CliConfig(permission=permission, provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )


def _pkg_root() -> Path:
    return Path(__file__).resolve().parents[1] / "pi_agent_cli"


def test_package_source_has_no_vendor_rpc_strings():
    hits: list[str] = []
    for path in _pkg_root().rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "x.ai/" in node.value
            ):
                hits.append(f"{path.name}:{node.lineno}:{node.value}")
    assert hits == []


@pytest.mark.asyncio
async def test_initialize_has_empty_auth_and_standard_session_caps(tmp_path):
    agent = _agent(tmp_path)
    client = FakeClient()
    agent.on_connect(client)
    resp = await agent.initialize(protocol_version=1)
    dumped = resp.model_dump(by_alias=True)
    assert dumped["authMethods"] == []
    assert "x.ai" not in str(dumped)
    assert resp.agent_capabilities.load_session is True
    assert resp.agent_capabilities.session_capabilities.list is not None
    assert resp.agent_capabilities.session_capabilities.resume is not None
    assert resp.agent_capabilities.session_capabilities.close is not None


@pytest.mark.asyncio
async def test_message_end_error_surfaces_in_session_update(tmp_path):
    from pi_agent_core.messages import AssistantMessage
    from pi_agent_core.types import MessageEndEvent

    agent = _agent(tmp_path)
    client = FakeClient()
    agent.on_connect(client)
    err_msg = AssistantMessage(
        role="assistant",
        content=[],
        api="langchain",
        provider="mock",
        model="mock",
        usage={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0},
        stopReason="error",
        errorMessage="Missing credentials",
        timestamp=0,
    )
    await agent._emit_updates("sess", MessageEndEvent(message=err_msg))
    texts = [
        getattr(u.content, "text", None)
        for _, u in client.updates
        if getattr(u, "session_update", None) == "agent_message_chunk"
    ]
    assert any("Missing credentials" in (t or "") for t in texts)


@pytest.mark.asyncio
async def test_ext_method_does_not_register_vendor_rpcs(tmp_path):
    agent = _agent(tmp_path)
    with pytest.raises(RequestError) as exc:
        await agent.ext_method("x.ai/session/list", {})
    assert exc.value.code == -32601
    with pytest.raises(RequestError):
        await agent.ext_method("x.ai/auth/get_url", {})
    await agent.ext_notification("x.ai/queue/changed", {})


@pytest.mark.asyncio
async def test_prompt_projects_text_deltas(tmp_path):
    agent = _agent(tmp_path)
    client = FakeClient()
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    result = await agent.prompt(session_id=created.session_id, prompt=[text_block("hello")])
    assert result.stop_reason == "end_turn"
    kinds = [getattr(u, "session_update", None) for _, u in client.updates]
    assert "agent_message_chunk" in kinds
    texts = [
        getattr(getattr(u, "content", None), "text", None)
        for _, u in client.updates
        if getattr(u, "session_update", None) == "agent_message_chunk"
    ]
    assert "Hello from mock" in "".join(t or "" for t in texts)


@pytest.mark.asyncio
async def test_list_load_close_session(tmp_path):
    agent = _agent(tmp_path)
    agent.on_connect(FakeClient())
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    listed = await agent.list_sessions()
    match = next(s for s in listed.sessions if s.session_id == created.session_id)
    assert match.title
    loaded = await agent.load_session(cwd=cwd, session_id=created.session_id)
    assert loaded is not None
    await agent.close_session(session_id=created.session_id)
    with pytest.raises(RequestError):
        await agent.prompt(session_id=created.session_id, prompt=[text_block("x")])


async def _bash_once_stream(model, context, options=None):
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)
    stream = AssistantMessageEventStream()
    tc: ToolCallContent = {
        "type": "toolCall",
        "id": "call_bash",
        "name": "bash",
        "arguments": {"command": "echo should-not-run"},
    }
    partial = _base_partial(model, [tc])
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


@pytest.mark.asyncio
async def test_permission_ask_denies_bash(tmp_path):
    agent = _agent(tmp_path, stream_fn=_bash_once_stream, permission="ask")
    client = FakeClient(allow=False)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    await agent.prompt(session_id=created.session_id, prompt=[text_block("run")])
    assert client.permission_calls
    assert client.permission_calls[0]["tool_call"].title == "bash"
    kinds = [getattr(u, "session_update", None) for _, u in client.updates]
    assert "tool_call" in kinds
    assert "tool_call_update" in kinds


@pytest.mark.asyncio
async def test_permission_auto_skips_request(tmp_path):
    agent = _agent(tmp_path, stream_fn=mock_text_stream, permission="auto")
    client = FakeClient(allow=False)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    assert client.permission_calls == []


def test_tool_kind_mapping():
    assert tool_kind("read") == "read"
    assert tool_kind("edit") == "edit"
    assert tool_kind("write") == "edit"
    assert tool_kind("bash") == "execute"
    assert tool_kind("grep") == "search"
    assert {"bash", "edit", "write"} == PERMISSION_TOOLS
    assert needs_permission("bash", "ask")
    assert not needs_permission("read", "ask")
    assert not needs_permission("bash", "always-approve")


def test_project_event_text_delta():
    from pi_agent_core.messages import AssistantMessage
    from pi_agent_core.types import MessageUpdateEvent, TextDeltaEvent

    msg = AssistantMessage(content=[{"type": "text", "text": "ab"}])
    event = MessageUpdateEvent(
        message=msg,
        assistant_message_event=TextDeltaEvent(partial=msg, delta="ab"),
    )
    updates = list(project_event(event))
    assert len(updates) == 1
    assert updates[0].session_update == "agent_message_chunk"
    assert updates[0].content.text == "ab"


def test_project_event_thinking_delta():
    from pi_agent_core.messages import AssistantMessage
    from pi_agent_core.types import MessageUpdateEvent, ThinkingDeltaEvent

    msg = AssistantMessage(content=[{"type": "thinking", "thinking": "hmm"}])
    event = MessageUpdateEvent(
        message=msg,
        assistant_message_event=ThinkingDeltaEvent(partial=msg, delta="hmm"),
    )
    updates = list(project_event(event))
    assert len(updates) == 1
    assert updates[0].session_update == "agent_thought_chunk"
    assert updates[0].content.text == "hmm"


@pytest.mark.asyncio
async def test_cancel_is_noop_for_unknown_and_safe_for_bound_session(tmp_path):
    agent = _agent(tmp_path)
    agent.on_connect(FakeClient())
    await agent.cancel(session_id="missing")
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    await agent.cancel(session_id=created.session_id)


def test_stop_reason_mapping():
    from pi_agent_cli.agent import _stop_reason
    from pi_agent_core.messages import AssistantMessage

    assert _stop_reason(AssistantMessage(content=[], stopReason="stop")) == "end_turn"
    assert _stop_reason(AssistantMessage(content=[], stopReason="aborted")) == "cancelled"
    assert _stop_reason(AssistantMessage(content=[], stopReason="length")) == "max_tokens"
    assert _stop_reason(AssistantMessage(content=[], stopReason="error")) == "end_turn"
    assert (
        _stop_reason(
            AssistantMessage(content=[], stopReason="error", errorMessage="Content policy refusal")
        )
        == "refusal"
    )
