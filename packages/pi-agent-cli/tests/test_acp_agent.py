"""ACP agent: standard methods, event projection, permission hook, no vendor RPCs."""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError, text_block
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig, ModelChoice
from pi_agent_cli.events import project_event, tool_kind
from pi_agent_cli.permissions import needs_permission, permission_tool_call
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
async def test_session_responses_include_model_meta(tmp_path):
    agent = _agent(tmp_path)
    agent.on_connect(FakeClient())
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    assert created.field_meta is not None
    assert created.field_meta.get("pi/currentModelId") == "mock"

    loaded = await agent.load_session(cwd=cwd, session_id=created.session_id)
    assert loaded is not None
    assert loaded.field_meta is not None
    assert loaded.field_meta.get("pi/currentModelId") == "mock"

    resumed = await agent.resume_session(session_id=created.session_id, cwd=cwd)
    assert resumed.field_meta is not None
    assert resumed.field_meta.get("pi/currentModelId") == "mock"


def _multi_model_agent(tmp_path: Path) -> PiAcpAgent:
    return PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path,
        config=CliConfig(
            provider="mock",
            model_id="mock",
            models=(ModelChoice(id="mock-pro", name="Mock Pro"),),
        ),
    )


def _model_option(options):
    assert options is not None
    (option,) = [o for o in options if o.id == "model"]
    return option


@pytest.mark.asyncio
async def test_session_responses_advertise_model_config_option(tmp_path):
    agent = _multi_model_agent(tmp_path)
    agent.on_connect(FakeClient())
    cwd = str(tmp_path.resolve())

    created = await agent.new_session(cwd=cwd)
    option = _model_option(created.config_options)
    assert option.type == "select"
    assert option.category == "model"
    assert option.current_value == "mock"
    assert [(o.value, o.name) for o in option.options] == [
        ("mock", "mock"),
        ("mock-pro", "Mock Pro"),
    ]

    loaded = await agent.load_session(cwd=cwd, session_id=created.session_id)
    assert loaded is not None
    assert _model_option(loaded.config_options).current_value == "mock"
    resumed = await agent.resume_session(session_id=created.session_id, cwd=cwd)
    assert _model_option(resumed.config_options).current_value == "mock"


@pytest.mark.asyncio
async def test_set_config_option_switches_model_and_persists(tmp_path):
    agent = _multi_model_agent(tmp_path)
    agent.on_connect(FakeClient())
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    sid = created.session_id

    resp = await agent.set_config_option(config_id="model", session_id=sid, value="mock-pro")
    assert resp is not None
    assert _model_option(resp.config_options).current_value == "mock-pro"
    assert agent._harnesses[sid].model.model_id == "mock-pro"
    # legacy pi/* hints follow the switch
    assert agent._session_response_meta(sid)["pi/currentModelId"] == "mock-pro"
    assert agent._session_response_meta(sid)["pi/currentModelDisplayName"] == "Mock Pro"

    # a fresh agent process resuming the same session restores the persisted model
    agent2 = _multi_model_agent(tmp_path)
    agent2.on_connect(FakeClient())
    resumed = await agent2.resume_session(session_id=sid, cwd=cwd)
    assert _model_option(resumed.config_options).current_value == "mock-pro"
    assert agent2._harnesses[sid].model.model_id == "mock-pro"


@pytest.mark.asyncio
async def test_a_chosen_model_keeps_the_reasoning_setting_and_gets_only_its_own_key(
    tmp_path, monkeypatch
):
    """``/model`` meets two other rules: ``Model.reasoning`` follows the configured thinking
    level whichever model is active, and an ``api_key_env`` is the key of its own provider only."""
    monkeypatch.setenv("DEFAULT_KEY", "default-secret")
    monkeypatch.setenv("OTHER_KEY", "other-secret")
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path,
        config=CliConfig(
            provider="mock",
            model_id="mock",
            api_key_env="DEFAULT_KEY",
            thinking_level="high",
            models=(ModelChoice(id="other", provider="otherco", api_key_env="OTHER_KEY"),),
        ),
    )
    agent.on_connect(FakeClient())
    sid = (await agent.new_session(cwd=str(tmp_path.resolve()))).session_id
    harness = agent._harnesses[sid]

    assert harness.model.reasoning is True
    assert harness.get_api_key("mock") == "default-secret"
    assert harness.get_api_key("otherco") is None

    await agent.set_config_option(config_id="model", session_id=sid, value="other")

    assert harness.model.provider == "otherco"
    assert harness.model.reasoning is True
    assert harness.get_api_key("otherco") == "other-secret"
    assert harness.get_api_key("mock") is None


@pytest.mark.asyncio
async def test_set_config_option_rejects_unknown_option_and_model(tmp_path):
    agent = _multi_model_agent(tmp_path)
    agent.on_connect(FakeClient())
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    sid = created.session_id

    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="nope", session_id=sid, value="mock")
    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="model", session_id=sid, value="missing")
    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="model", session_id=sid, value=True)
    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="model", session_id="unknown", value="mock")
    assert agent._harnesses[sid].model.model_id == "mock"


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
async def test_ext_method_pi_session_delete_removes_repo_session(tmp_path):
    agent = _agent(tmp_path)
    agent.on_connect(FakeClient())
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    before = await agent.list_sessions()
    assert any(s.session_id == created.session_id for s in before.sessions)

    deleted = await agent.ext_method(
        "pi/session/delete",
        {"sessionId": created.session_id, "cwd": cwd, "source": "local"},
    )
    assert deleted["sessionId"] == created.session_id
    assert deleted["deleted"] is True

    after = await agent.list_sessions()
    assert all(s.session_id != created.session_id for s in after.sessions)

    deleted_again = await agent.ext_method("pi/session/delete", {"sessionId": created.session_id})
    assert deleted_again["deleted"] is True


@pytest.mark.asyncio
async def test_ext_method_pi_session_delete_requires_session_id(tmp_path):
    agent = _agent(tmp_path)
    with pytest.raises(RequestError):
        await agent.ext_method("pi/session/delete", {})


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


@pytest.mark.asyncio
async def test_list_sessions_titles_follow_the_first_prompt(tmp_path):
    agent = _agent(tmp_path)
    agent.on_connect(FakeClient())
    cwd = str(tmp_path.resolve())
    empty = await agent.new_session(cwd=cwd)
    talked = await agent.new_session(cwd=cwd)
    await agent.prompt(session_id=talked.session_id, prompt=[text_block("explain\n  the   build")])

    listed = {s.session_id: s for s in (await agent.list_sessions()).sessions}
    assert listed[empty.session_id].title == "(no messages)"
    assert listed[talked.session_id].title == "explain the build"
    updated = datetime.fromisoformat(listed[talked.session_id].updated_at.replace("Z", "+00:00"))
    assert updated.tzinfo is not None


@pytest.mark.asyncio
async def test_load_session_replays_history(tmp_path):
    client = FakeClient()
    agent = _agent(tmp_path)
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    await agent.close_session(session_id=created.session_id)

    client.updates.clear()
    loaded = await agent.load_session(cwd=cwd, session_id=created.session_id)
    assert loaded is not None
    assert len(client.updates) > 0
    for sid, update in client.updates:
        assert sid == created.session_id
        # AvailableCommandsUpdate is infrastructure, not replay content
        if getattr(update, "session_update", None) == "available_commands_update":
            continue
        meta = getattr(update, "field_meta", None)
        assert meta is not None and meta.get("isReplay") is True


@pytest.mark.asyncio
async def test_resume_session_does_not_replay_history(tmp_path):
    client = FakeClient()
    agent = _agent(tmp_path)
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    await agent.close_session(session_id=created.session_id)

    client.updates.clear()
    resumed = await agent.resume_session(session_id=created.session_id, cwd=cwd)
    assert resumed is not None
    # AvailableCommandsUpdate is infrastructure (command advertisement) — not replay
    replay_updates = [
        u
        for _, u in client.updates
        if getattr(u, "session_update", None) != "available_commands_update"
    ]
    assert replay_updates == []


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


@pytest.mark.asyncio
async def test_permission_mode_notification_updates_live_session_policy(tmp_path):
    agent = _agent(tmp_path, stream_fn=_bash_once_stream, permission="ask")
    client = FakeClient(allow=False)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    await agent.ext_notification(
        "x.ai/yolo_mode_changed",
        {"permission_mode": "auto"},
    )
    await agent.prompt(session_id=created.session_id, prompt=[text_block("run")])
    assert client.permission_calls == []


@pytest.mark.asyncio
async def test_invalid_permission_mode_notification_is_ignored(tmp_path):
    agent = _agent(tmp_path, stream_fn=_bash_once_stream, permission="ask")
    client = FakeClient(allow=False)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    await agent.ext_notification("x.ai/yolo_mode_changed", {"permission_mode": "bogus"})
    await agent.prompt(session_id=created.session_id, prompt=[text_block("run")])
    assert client.permission_calls


def test_tool_kind_mapping():
    assert tool_kind("read") == "read"
    assert tool_kind("edit") == "edit"
    assert tool_kind("write") == "edit"
    assert tool_kind("bash") == "execute"
    assert tool_kind("grep") == "search"


def test_permission_ask_mode_asks_unless_the_tool_declares_itself_harmless():
    assert needs_permission("ask", None)
    assert needs_permission("ask", {"readOnlyHint": False})
    assert not needs_permission("ask", {"readOnlyHint": True})
    assert not needs_permission("always-approve", None)


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


def test_tool_title_extraction():
    from pi_agent_cli.events import tool_title

    assert tool_title("bash", {"command": "echo hello"}) == "echo hello"
    assert tool_title("bash", {}) == "bash"
    assert tool_title("bash", None) == "bash"
    assert tool_title("write", {"path": "a/b.py", "content": "..."}) == "write a/b.py"
    assert tool_title("read", {"path": "a/b.py"}) == "read a/b.py"
    assert tool_title("edit", {"path": "a/b.py"}) == "edit a/b.py"
    assert tool_title("grep", {"pattern": "foo", "path": "src"}) == "grep foo src"
    assert tool_title("grep", {"pattern": "foo"}) == "grep foo"
    assert tool_title("find", {"pattern": "*.py"}) == "find *.py"
    assert tool_title("ls", {"path": "src"}) == "ls src"
    assert tool_title("custom_tool", {"foo": "bar"}) == "custom_tool"


def test_project_event_bash_lifecycle_terminal_meta():
    from pi_agent_core.types import (
        AgentToolResult,
        ToolExecutionEndEvent,
        ToolExecutionStartEvent,
        ToolExecutionUpdateEvent,
    )

    # 1. start
    start_ev = ToolExecutionStartEvent(
        tool_call_id="bash_123",
        tool_name="bash",
        args={"command": "python zh_experiment.py"},
    )
    start_updates = list(project_event(start_ev, cwd="/workspace/proj"))
    assert len(start_updates) == 1
    u_start = start_updates[0]
    assert u_start.session_update == "tool_call"
    assert u_start.title == "python zh_experiment.py"
    assert u_start.kind == "execute"
    assert u_start.field_meta == {
        "terminal_info": {
            "terminal_id": "bash_123",
            "cwd": "/workspace/proj",
        }
    }
    assert u_start.content[0].type == "terminal"
    assert u_start.content[0].terminal_id == "bash_123"

    # 2. update 1 (partial stdout)
    upd_ev1 = ToolExecutionUpdateEvent(
        tool_call_id="bash_123",
        tool_name="bash",
        partial_result=AgentToolResult(
            content=[{"type": "text", "text": "Running test 1\n"}],
            details=None,
        ),
        args={"command": "python zh_experiment.py"},
    )
    upd_updates1 = list(project_event(upd_ev1))
    assert len(upd_updates1) == 1
    u_upd1 = upd_updates1[0]
    assert u_upd1.session_update == "tool_call_update"
    assert u_upd1.field_meta == {
        "terminal_output": {
            "terminal_id": "bash_123",
            "data": "Running test 1\n",
        }
    }
    assert any(c.type == "terminal" for c in u_upd1.content)
    assert any(
        getattr(c, "content", None) and c.content.text == "Running test 1\n" for c in u_upd1.content
    )

    # 2. update 2 (incremental delta)
    upd_ev2 = ToolExecutionUpdateEvent(
        tool_call_id="bash_123",
        tool_name="bash",
        partial_result=AgentToolResult(
            content=[{"type": "text", "text": "Running test 1\nRunning test 2\n"}],
            details=None,
        ),
        args={"command": "python zh_experiment.py"},
    )
    upd_updates2 = list(project_event(upd_ev2))
    assert len(upd_updates2) == 1
    u_upd2 = upd_updates2[0]
    assert u_upd2.field_meta == {
        "terminal_output": {
            "terminal_id": "bash_123",
            "data": "Running test 2\n",
        }
    }

    # 3. end (completed with remaining delta or exit)
    end_ev = ToolExecutionEndEvent(
        tool_call_id="bash_123",
        tool_name="bash",
        result=AgentToolResult(
            content=[{"type": "text", "text": "Running test 1\nRunning test 2\nDone.\n"}],
            details=None,
        ),
        is_error=False,
    )
    end_updates = list(project_event(end_ev))
    assert len(end_updates) == 1
    u_end = end_updates[0]
    assert u_end.session_update == "tool_call_update"
    assert u_end.status == "completed"
    assert u_end.field_meta == {
        "terminal_exit": {
            "terminal_id": "bash_123",
            "exit_code": 0,
        },
        "terminal_output": {
            "terminal_id": "bash_123",
            "data": "Done.\n",
        },
    }
    assert any(c.type == "terminal" for c in u_end.content)
    assert any(
        getattr(c, "content", None) and c.content.text.startswith("Running test")
        for c in u_end.content
    )


def test_project_message_replay_bash():
    from pi_agent_cli.events import project_message_replay
    from pi_agent_core.messages import AssistantMessage, ToolResultMessage

    asst_msg = AssistantMessage(
        content=[
            {
                "type": "toolCall",
                "id": "tc_replay_bash",
                "name": "bash",
                "arguments": {"command": "pytest -v"},
            }
        ]
    )
    asst_updates = list(project_message_replay(asst_msg))
    assert len(asst_updates) == 1
    u_tc = asst_updates[0]
    assert u_tc.session_update == "tool_call"
    assert u_tc.title == "pytest -v"
    assert u_tc.field_meta.get("terminal_info") == {"terminal_id": "tc_replay_bash"}

    result_msg = ToolResultMessage(
        toolCallId="tc_replay_bash",
        toolName="bash",
        content=[{"type": "text", "text": "collected 10 items\n10 passed"}],
        isError=False,
    )
    res_updates = list(project_message_replay(result_msg))
    assert len(res_updates) == 1
    u_res = res_updates[0]
    assert u_res.session_update == "tool_call_update"
    assert u_res.field_meta.get("terminal_output") == {
        "terminal_id": "tc_replay_bash",
        "data": "collected 10 items\n10 passed",
    }
    assert u_res.field_meta.get("terminal_exit") == {
        "terminal_id": "tc_replay_bash",
        "exit_code": 0,
    }


# ---------------------------------------------------------------------------
# Slash command routing via ACP (P7-07 fix)
# ---------------------------------------------------------------------------


def _cmd_ext(pi):
    """Extension that registers /hello and /status commands."""
    pi.register_command(
        "hello",
        description="Say hello",
        handler=lambda args: pi.send_message(f"Hi, {args.strip() or 'there'}!"),
    )
    pi.register_command(
        "status",
        description="Show status",
        handler=lambda _args: pi.send_message("All systems operational."),
    )


def _agent_with_ext(tmp_path: Path) -> PiAcpAgent:
    return PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path,
        config=CliConfig(permission="auto", provider="mock", model_id="mock"),
        extensions=[_cmd_ext],
    )


@pytest.mark.asyncio
async def test_slash_command_advertised_on_session_bind(tmp_path):
    """AvailableCommandsUpdate is sent after session is created (deferred)."""
    agent = _agent_with_ext(tmp_path)
    client = FakeClient()
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    await agent.new_session(cwd=cwd)
    # Commands are sent via asyncio.create_task + sleep(0) to work around
    # the Zed race condition (zed#60199).  Two yields: one to let the
    # deferred task wake from its sleep(0), another to let it complete.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    acu = [
        u
        for _, u in client.updates
        if getattr(u, "session_update", None) == "available_commands_update"
    ]
    assert len(acu) == 1
    names = {c.name for c in acu[0].available_commands}
    assert "hello" in names
    assert "status" in names


@pytest.mark.asyncio
async def test_slash_command_dispatched_via_acp_prompt(tmp_path):
    """prompt('/hello World') dispatches to handler and returns end_turn."""
    agent = _agent_with_ext(tmp_path)
    client = FakeClient()
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    result = await agent.prompt(session_id=created.session_id, prompt=[text_block("/hello World")])
    assert result.stop_reason == "end_turn"

    # Check that the agent message chunk was emitted with the handler output
    msg_chunks = [
        u for _, u in client.updates if getattr(u, "session_update", None) == "agent_message_chunk"
    ]
    texts = [getattr(getattr(u, "content", None), "text", None) or "" for u in msg_chunks]
    assert "Hi, World!" in "".join(texts)


@pytest.mark.asyncio
async def test_slash_command_unknown_falls_through_via_acp(tmp_path):
    """prompt('/bogus') is not a registered command — passed to LLM."""
    agent = _agent_with_ext(tmp_path)
    client = FakeClient()
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)
    result = await agent.prompt(
        session_id=created.session_id, prompt=[text_block("/bogus something")]
    )
    assert result.stop_reason == "end_turn"

    msg_chunks = [
        u for _, u in client.updates if getattr(u, "session_update", None) == "agent_message_chunk"
    ]
    texts = [getattr(getattr(u, "content", None), "text", None) or "" for u in msg_chunks]
    # LLM produces "Hello from mock"
    assert "Hello from mock" in "".join(texts)


# ---------------------------------------------------------------------------
# Workflow sub-agents obey the session's permission policy (audit P7-01)
# ---------------------------------------------------------------------------
#
# A workflow fans work out to sub-agents that run on harnesses of their own. Those used
# to carry no hooks, so in "ask" mode a sub-agent's bash/edit/write ran without a single
# prompt. Now the ``workflow`` call is gated like bash/edit/write, and every tool call a
# sub-agent makes goes through the same permission layer the session itself uses.


def test_workflow_needs_permission_in_ask_mode_only():
    workflow = pytest.importorskip("pi_dynamic_workflows.workflow_tool").create_workflow_tool()
    assert needs_permission("ask", workflow.annotations)
    assert not needs_permission("auto", workflow.annotations)
    assert not needs_permission("always-approve", workflow.annotations)


def test_permission_prompt_says_where_a_subagent_call_runs():
    plain = permission_tool_call("c1", "write", {"path": "a.txt"})
    assert plain.title == "write"

    origin = {"kind": "subagent", "cwd": "/srv/elsewhere", "label": "trusted-setup"}
    prompt = permission_tool_call("c2", "write", {"path": "a.txt"}, origin=origin)

    assert prompt.title.split()[0] == "write"
    assert "sub-agent" in prompt.title
    assert "/srv/elsewhere" in prompt.title  # a relative path means nothing without this
    assert "trusted-setup" not in prompt.title  # the label is text the workflow script chose
    assert prompt.raw_input == {"path": "a.txt"}  # what the tool will receive, untouched
    assert prompt.kind == plain.kind


def _tool_call_stream(model, name: str, arguments: dict[str, Any], call_id: str = "call_1"):
    stream = AssistantMessageEventStream()
    tc: ToolCallContent = {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}
    partial = _base_partial(model, [tc])
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


def _first_user_text(context: Any) -> str:
    content = context.messages[0].content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class _Scenario:
    """Scripted LLM: the session runs one workflow whose sub-agents each write a file.

    A sub-agent's prompt is the path it must write; every call uses the tool call id
    ``call_1``, like the parent's ``workflow`` call, so id clashes are visible.
    """

    def __init__(self, script: str, args: dict[str, Any]) -> None:
        self.script = script
        self.args = args
        self.writes_issued = 0  # sub-agent write calls asked for so far
        self.subagent_replies: list[tuple[bool, str]] = []  # what sub-agents were told

    async def stream(self, model, context, options=None):
        results = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
        if "workflow" in {tool.name for tool in context.tools or []}:  # the session itself
            if results:
                return await mock_text_stream(model, context, options)
            return _tool_call_stream(model, "workflow", {"script": self.script, "args": self.args})
        if results:  # a sub-agent, after its write was decided
            self.subagent_replies.extend((r.isError, r.content[0]["text"]) for r in results)
            return await mock_text_stream(model, context, options)
        target = _first_user_text(context)
        self.writes_issued += 1
        return _tool_call_stream(model, "write", {"path": target, "content": "written\n"})


_ONE_WRITER = (
    'meta = {"name": "one-writer"}\n'
    "async def main():\n"
    '    await agent(args["paths"][0])\n'
    '    result("done")\n'
)
_PARALLEL_WRITERS = (
    'meta = {"name": "parallel-writers"}\n'
    "async def main():\n"
    '    await parallel([lambda p=p: agent(p) for p in args["paths"]])\n'
    '    result("done")\n'
)
_WRITER_IN_OTHER_DIR = (
    'meta = {"name": "writer-elsewhere"}\n'
    "async def main():\n"
    '    await agent(args["paths"][0], cwd=args["cwd"])\n'
    '    result("done")\n'
)


class _PickyClient(FakeClient):
    """A user who rejects the tools named in ``reject`` and takes a moment to answer.

    ``hold_writes_until``: answer ``write`` prompts only once this returns true, so that
    prompts from parallel sub-agents provably overlap, unless something serializes them.
    """

    def __init__(self, reject: set[str] | None = None) -> None:
        super().__init__(allow=True)
        self.reject = reject or set()
        self.hold_writes_until: Any = None
        self.open_requests = 0
        self.max_open_requests = 0

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.open_requests += 1
        self.max_open_requests = max(self.max_open_requests, self.open_requests)
        try:
            self.permission_calls.append(
                {"session_id": session_id, "tool_call": tool_call, "options": options}
            )
            tool = tool_call.title.split()[0]
            await asyncio.sleep(0.01)
            if tool == "write" and self.hold_writes_until is not None:
                for _ in range(400):  # at most ~2s, so a broken test fails instead of hanging
                    if self.hold_writes_until():
                        break
                    await asyncio.sleep(0.005)
            if tool in self.reject:
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
            )
        finally:
            self.open_requests -= 1

    def asked_about(self) -> list[str]:
        return [call["tool_call"].title.split()[0] for call in self.permission_calls]


async def _run_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    script: str,
    args: dict[str, Any],
    client: FakeClient,
    permission: str = "ask",
    workspace: Path | None = None,
    scenario: _Scenario | None = None,
) -> tuple[_Scenario, PiAcpAgent, str]:
    pytest.importorskip("pi_dynamic_workflows")
    # Workflow journals and extension discovery use Path.home(); keep both off the real HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    scenario = scenario or _Scenario(script, args)
    agent = _agent(tmp_path, stream_fn=scenario.stream, permission=permission)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str((workspace or tmp_path).resolve()))
    await agent.prompt(session_id=created.session_id, prompt=[text_block("run the workflow")])
    return scenario, agent, created.session_id


@pytest.mark.asyncio
async def test_workflow_call_needs_permission_and_a_denial_stops_it(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    client = _PickyClient(reject={"workflow"})

    await _run_workflow(
        tmp_path, monkeypatch, script=_ONE_WRITER, args={"paths": [str(target)]}, client=client
    )

    assert client.asked_about() == ["workflow"]
    assert not target.exists()  # the workflow never started, so no sub-agent got to write


@pytest.mark.asyncio
async def test_subagent_write_is_authorized_per_call_and_a_denial_holds(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    client = _PickyClient(reject={"write"})

    scenario, _, _ = await _run_workflow(
        tmp_path, monkeypatch, script=_ONE_WRITER, args={"paths": [str(target)]}, client=client
    )

    assert client.asked_about() == ["workflow", "write"]
    assert not target.exists()
    ((is_error, text),) = scenario.subagent_replies  # the sub-agent was told, and carried on
    assert is_error is True
    assert "User denied permission" in text


@pytest.mark.asyncio
async def test_subagent_write_runs_once_the_user_allows_it(tmp_path, monkeypatch):
    """Control for the denial test: the write really happens when it is allowed."""
    target = tmp_path / "a.txt"
    client = _PickyClient()

    await _run_workflow(
        tmp_path, monkeypatch, script=_ONE_WRITER, args={"paths": [str(target)]}, client=client
    )

    assert client.asked_about() == ["workflow", "write"]
    assert target.read_text(encoding="utf-8") == "written\n"


@pytest.mark.asyncio
async def test_permission_auto_lets_workflow_subagents_run_without_prompts(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    client = _PickyClient(reject={"workflow", "write"})  # would refuse, if it were asked

    await _run_workflow(
        tmp_path,
        monkeypatch,
        script=_ONE_WRITER,
        args={"paths": [str(target)]},
        client=client,
        permission="auto",
    )

    assert client.permission_calls == []
    assert target.read_text(encoding="utf-8") == "written\n"


@pytest.mark.asyncio
async def test_subagent_permission_requests_do_not_reuse_the_sessions_tool_call_ids(
    tmp_path, monkeypatch
):
    """Every call here is named ``call_1`` (the parent's workflow call, and both sub-agents'
    writes). The client keys its permission cards by id, so ids must not collide."""
    paths = [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]
    client = _PickyClient()

    await _run_workflow(
        tmp_path, monkeypatch, script=_PARALLEL_WRITERS, args={"paths": paths}, client=client
    )

    ids = [call["tool_call"].tool_call_id for call in client.permission_calls]
    assert ids[0] == "call_1"  # the session's own workflow call keeps its id
    write_ids = ids[1:]
    assert len(write_ids) == 2
    assert len(set(write_ids)) == 2
    assert "call_1" not in write_ids


@pytest.mark.asyncio
async def test_parallel_subagents_get_one_permission_prompt_at_a_time(tmp_path, monkeypatch):
    """Parallel sub-agents would otherwise open several prompts at once, and a client
    that shows one dialog at a time would leave the others unanswered forever."""
    paths = [str(tmp_path / f"{name}.txt") for name in ("a", "b", "c")]
    scenario = _Scenario(_PARALLEL_WRITERS, {"paths": paths})
    client = _PickyClient()
    # Hold every write prompt open until all three sub-agents have asked to write, so the
    # prompts overlap unless the agent serializes them.
    client.hold_writes_until = lambda: scenario.writes_issued >= 3

    await _run_workflow(
        tmp_path,
        monkeypatch,
        script=_PARALLEL_WRITERS,
        args={"paths": paths},
        client=client,
        scenario=scenario,
    )

    assert client.asked_about() == ["workflow", "write", "write", "write"]
    assert client.max_open_requests == 1
    assert all(Path(p).exists() for p in paths)


@pytest.mark.asyncio
async def test_subagent_prompt_names_the_directory_the_call_runs_in(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    package = workspace / "pkg"  # inside the workspace, but not where the session started
    workspace.mkdir()
    package.mkdir()
    client = _PickyClient()

    _, agent, session_id = await _run_workflow(
        tmp_path,
        monkeypatch,
        script=_WRITER_IN_OTHER_DIR,
        args={"paths": ["note.txt"], "cwd": str(package)},
        client=client,
        workspace=workspace,
    )

    workflow_prompt, write_prompt = (call["tool_call"] for call in client.permission_calls)
    assert workflow_prompt.title == "workflow"  # the session's own call carries no origin
    assert "sub-agent" in write_prompt.title
    assert str(package) in write_prompt.title  # the script chose this, not the session
    assert (package / "note.txt").exists()
    assert session_id in agent._session_cwds


@pytest.mark.asyncio
async def test_a_workflow_cannot_send_a_subagent_outside_the_workspace(tmp_path, monkeypatch):
    """The prompt names the directory, but a "yes" to a write must not reach beyond the project."""
    workspace = tmp_path / "workspace"
    elsewhere = tmp_path / "elsewhere"
    workspace.mkdir()
    elsewhere.mkdir()
    client = _PickyClient()

    await _run_workflow(
        tmp_path,
        monkeypatch,
        script=_WRITER_IN_OTHER_DIR,
        args={"paths": ["note.txt"], "cwd": str(elsewhere)},
        client=client,
        workspace=workspace,
    )

    assert client.asked_about() == ["workflow"]  # no sub-agent ever got as far as a write
    assert not (elsewhere / "note.txt").exists()
