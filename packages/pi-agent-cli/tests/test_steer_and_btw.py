"""Tests for Pre-Refactor X9 (pi/session/steer) and X10 (pi/session/btw)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError
from acp.helpers import text_block
from acp.schema import ClientCapabilities

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.btw import (
    BTW_MAX_HISTORY_BYTES,
    BTW_MAX_HISTORY_TURNS,
    flatten_tool_history_for_btw,
    parse_btw_history,
    repair_dangling_tool_calls,
)
from pi_agent_cli.config import CliConfig, load_config
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions.types import ToolDefinition
from pi_agent_core.messages import (
    AssistantMessage,
    ToolCallContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from pi_agent_core.types import (
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolAnnotations,
    ToolCallStartEvent,
)
from pi_agent_harness import DeferredJsonlSessionStorage


class _RecordingClient:
    """Fake ACP client that captures both session_update and ext_notification."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, Any]] = []
        self.ext_notifications: list[tuple[str, dict[str, Any]]] = []
        self.permission_requests: list[tuple[str, Any, list[Any]]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append((session_id, update))

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notifications.append((method, params))

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any
    ) -> Any:
        from acp.schema import AllowedOutcome, RequestPermissionResponse

        self.permission_requests.append((session_id, tool_call, options))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(option_id="allow_once", outcome="selected")
        )


def _assistant_msg(
    model: Model, content: list[Any], *, stop_reason: str = "stop"
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api=model.api,
        provider=model.provider,
        model=model.model_id,
        usage=Usage(),
        stopReason=stop_reason,  # type: ignore[arg-type]
    )


def _text_stream(model: Model, text: str, *, thinking: str = "") -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    content: list[Any] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    content.append({"type": "text", "text": text})
    final = _assistant_msg(model, content, stop_reason="stop")
    stream.push(StartEvent(partial=final.model_copy(deep=True)))
    if thinking:
        stream.push(ThinkingDeltaEvent(content_index=0, delta=thinking, partial=final))
    stream.push(TextDeltaEvent(content_index=len(content) - 1, delta=text, partial=final))
    stream.push(DoneEvent(partial=final, reason="stop"))
    stream.set_final_message(final)
    stream.end()
    return stream


def _tool_call_stream(model: Model, tc_id: str, name: str, args: dict[str, Any]):
    stream = AssistantMessageEventStream()
    tc: ToolCallContent = {"type": "toolCall", "id": tc_id, "name": name, "arguments": args}
    final = _assistant_msg(model, [tc], stop_reason="toolUse")
    stream.push(StartEvent(partial=final.model_copy(deep=True)))
    stream.push(DoneEvent(partial=final, reason="toolUse"))
    stream.set_final_message(final)
    stream.end()
    return stream


def _make_agent(
    tmp_path: Path,
    stream_fn: Any,
    *,
    permission: str = "always-approve",
    extensions: list[Any] | None = None,
) -> PiAcpAgent:
    cfg = CliConfig(permission=permission)  # type: ignore[arg-type]
    return PiAcpAgent(stream_fn=stream_fn, home=tmp_path, config=cfg, extensions=extensions)


# ---------------------------------------------------------------------------
# Capabilities & config tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_advertises_steer_and_btw_capabilities(tmp_path: Path):
    agent = _make_agent(tmp_path, lambda m, c, o=None: _text_stream(m, "ok"))
    resp = await agent.initialize(protocol_version=1)
    assert resp.field_meta is not None
    caps = resp.field_meta.get("pi/capabilities")
    assert caps == {"steer": True, "btw": True}


@pytest.mark.asyncio
async def test_advertise_commands_btw_only_for_opt_in_clients(tmp_path: Path):
    # Standard third-party client without pi/capabilities.btw opt-in must NOT receive /btw.
    client_plain = _RecordingClient()
    agent_plain = _make_agent(tmp_path / "plain", lambda m, c, o=None: _text_stream(m, "ok"))
    agent_plain.on_connect(client_plain)
    await agent_plain.initialize(protocol_version=1, client_capabilities=ClientCapabilities())
    await agent_plain.new_session(cwd=str(tmp_path))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    plain_names: set[str] = set()
    for _, update in client_plain.updates:
        if getattr(update, "session_update", None) == "available_commands_update":
            plain_names.update(c.name for c in update.available_commands)
    assert "btw" not in plain_names

    # Opt-in client advertising clientCapabilities._meta["pi/capabilities"]["btw"] = True gets /btw.
    client_opt_in = _RecordingClient()
    agent_opt_in = _make_agent(tmp_path / "optin", lambda m, c, o=None: _text_stream(m, "ok"))
    agent_opt_in.on_connect(client_opt_in)
    await agent_opt_in.initialize(
        protocol_version=1,
        client_capabilities=ClientCapabilities(field_meta={"pi/capabilities": {"btw": True}}),
    )
    await agent_opt_in.new_session(cwd=str(tmp_path))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    opt_in_names: set[str] = set()
    for _, update in client_opt_in.updates:
        if getattr(update, "session_update", None) == "available_commands_update":
            opt_in_names.update(c.name for c in update.available_commands)
    assert "btw" in opt_in_names


@pytest.mark.asyncio
async def test_literal_btw_prompt_returns_notice_without_turn_or_jsonl(tmp_path: Path):
    llm_calls = 0

    def stream_fn(model: Model, context: Any, options: Any = None):
        nonlocal llm_calls
        llm_calls += 1
        return _text_stream(model, "should not run")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))

    res = await agent.prompt(
        session_id=created.session_id, prompt=[text_block("/btw what is exit 137?")]
    )
    assert res.stop_reason == "end_turn"
    assert llm_calls == 0

    harness = agent._harnesses[created.session_id]
    storage = harness.session.get_storage()
    assert isinstance(storage, DeferredJsonlSessionStorage)
    assert storage.written is False
    assert list((tmp_path / "sessions").glob("*.jsonl")) == []

    agent_texts = [
        u.content.text
        for _, u in client.updates
        if getattr(u, "session_update", None) == "agent_message_chunk"
    ]
    assert any("pi/session/btw" in t for t in agent_texts)


def test_btw_config_parsing(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "openai"\nid = "gpt-4o"\n\n'
        '[[models]]\nprovider = "openai"\nid = "gpt-4o-mini"\n\n'
        '[btw]\nmodel = "gpt-4o-mini"\nthinking = "inherit"\ntimeout_seconds = 45\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.btw.model == "gpt-4o-mini"
    assert cfg.btw.thinking == "inherit"
    assert cfg.btw.timeout_seconds == 45.0
    assert cfg.btw.notes == ()
    assert not cfg.btw_model_is_unknown()


def test_btw_config_unknown_model_records_note(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "openai"\nid = "gpt-4o"\n\n[btw]\nmodel = "does-not-exist"\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.btw_model_is_unknown()
    assert any("does-not-exist" in n for n in cfg.btw.notes)
    assert any("does-not-exist" in n for n in cfg.auto.notes)


def test_btw_config_invalid_values_fallback_with_notes(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "openai"\nid = "gpt-4o"\n\n'
        '[btw]\nthinking = "max"\ntimeout_seconds = 1\nunknown_key = true\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.btw.thinking == "off"
    assert cfg.btw.timeout_seconds == 120.0
    assert len(cfg.btw.notes) == 3


# ---------------------------------------------------------------------------
# X9: pi/session/steer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_when_idle_returns_not_queued_idle(tmp_path: Path):
    client = _RecordingClient()
    agent = _make_agent(tmp_path, lambda m, c, o=None: _text_stream(m, "ok"))
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))
    res = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": created.session_id,
            "steerId": "s-idle",
            "prompt": [{"type": "text", "text": "steer while idle"}],
        },
    )
    assert res == {
        "sessionId": created.session_id,
        "steerId": "s-idle",
        "queued": False,
        "reason": "idle",
    }


@pytest.mark.asyncio
async def test_steer_mid_turn_injects_at_turn_boundary_and_replays(tmp_path: Path):
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    seen_user_texts: list[str] = []

    def ext(pi: Any) -> None:
        async def slow_tool(
            tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None
        ):
            tool_started.set()
            await release_tool.wait()
            return {"content": [{"type": "text", "text": "tool done"}]}

        pi.register_tool(
            ToolDefinition(
                name="slow_probe",
                description="waits until released",
                parameters={"type": "object", "properties": {}},
                execute=slow_tool,
                annotations=ToolAnnotations(readOnlyHint=True),
            )
        )

    call_count = 0

    async def stream_fn(model: Model, context: Any, options: Any = None):
        nonlocal call_count
        call_count += 1
        user_texts = [
            "".join(
                b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                for b in (m.content if isinstance(m.content, list) else [])
            )
            for m in context.messages
            if getattr(m, "role", None) == "user"
        ]
        seen_user_texts.clear()
        seen_user_texts.extend(user_texts)
        if call_count == 1:
            return _tool_call_stream(model, "call_slow_1", "slow_probe", {})
        return _text_stream(model, "final with steer")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn, extensions=[ext])
    agent.on_connect(client)
    cwd = str(tmp_path.resolve())
    created = await agent.new_session(cwd=cwd)

    prompt_task = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("initial task")])
    )
    await asyncio.wait_for(tool_started.wait(), timeout=5.0)

    steer_res = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": created.session_id,
            "steerId": "steer-1",
            "prompt": [{"type": "text", "text": "focus only on tests"}],
        },
    )
    assert steer_res == {
        "sessionId": created.session_id,
        "steerId": "steer-1",
        "queued": True,
        "queueLength": 1,
    }
    queue_notifs = [p for m, p in client.ext_notifications if m == "pi/queue_update"]
    assert any(
        n["sessionId"] == created.session_id
        and len(n["steer"]) == 1
        and n["steer"][0]["steerId"] == "steer-1"
        for n in queue_notifs
    )

    release_tool.set()
    prompt_res = await asyncio.wait_for(prompt_task, timeout=5.0)
    assert prompt_res.stop_reason == "end_turn"
    assert "focus only on tests" in seen_user_texts

    # Only the steered user message is emitted live with pi/steered=True (not the opening prompt).
    live_user_chunks = [
        u for _, u in client.updates if getattr(u, "session_update", None) == "user_message_chunk"
    ]
    assert len(live_user_chunks) == 1
    assert live_user_chunks[0].field_meta == {"pi/steered": True, "pi/steerId": "steer-1"}
    assert live_user_chunks[0].content.text == "focus only on tests"

    # Session replay restores both opening prompt and steered message in order.
    await agent.close_session(session_id=created.session_id)
    client.updates.clear()
    await agent.load_session(cwd=cwd, session_id=created.session_id)
    replayed_users = [
        u.content.text
        for _, u in client.updates
        if getattr(u, "session_update", None) == "user_message_chunk"
    ]
    assert replayed_users == ["initial task", "focus only on tests"]


@pytest.mark.asyncio
async def test_steer_auto_safety_cancelled_steer_not_noted_but_injected_is(tmp_path: Path):
    """Invariant S2 & S5: cancelled steer is cleared without Auto note; injected steer is noted."""
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    def ext(pi: Any) -> None:
        async def hold_tool(
            tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None
        ):
            tool_started.set()
            try:
                await release_tool.wait()
            except asyncio.CancelledError:
                raise
            return {"content": [{"type": "text", "text": "ok"}]}

        pi.register_tool(
            ToolDefinition(
                name="hold_probe",
                description="waits",
                parameters={"type": "object", "properties": {}},
                execute=hold_tool,
                annotations=ToolAnnotations(readOnlyHint=True),
            )
        )

    should_call_tool = True

    async def stream_fn(model: Model, context: Any, options: Any = None):
        nonlocal should_call_tool
        if should_call_tool:
            should_call_tool = False
            return _tool_call_stream(model, "call_hold", "hold_probe", {})
        return _text_stream(model, "done")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn, permission="auto", extensions=[ext])
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))
    auto = agent._autos[created.session_id]

    noted: list[str] = []
    orig_note = auto.note_user_message

    def spy_note(text: str) -> None:
        noted.append(text)
        orig_note(text)

    auto.note_user_message = spy_note  # type: ignore[method-assign]

    # Case 1: Enqueue steer, then cancel before tool finishes -> must NOT call note_user_message.
    prompt_task = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("first prompt")])
    )
    await asyncio.wait_for(tool_started.wait(), timeout=5.0)
    await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": created.session_id,
            "steerId": "s-aborted",
            "prompt": [{"type": "text", "text": "please run rm -rf build"}],
        },
    )
    assert "please run rm -rf build" not in noted

    await agent.cancel(session_id=created.session_id)
    release_tool.set()
    await asyncio.wait_for(prompt_task, timeout=5.0)
    # Wait for abort background task to finish notifying.
    if agent._abort_tasks:
        await asyncio.gather(*list(agent._abort_tasks), return_exceptions=True)

    assert "please run rm -rf build" not in noted
    abort_notifs = [
        p
        for m, p in client.ext_notifications
        if m == "pi/queue_update" and p.get("clearedByAbort") is True
    ]
    assert abort_notifs
    assert "s-aborted" in (abort_notifs[-1].get("clearedSteerIds") or [])

    # Case 2: Steer that IS injected -> MUST call note_user_message on injection.
    should_call_tool = True
    tool_started.clear()
    release_tool.clear()
    prompt_task2 = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("second prompt")])
    )
    await asyncio.wait_for(tool_started.wait(), timeout=5.0)
    await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": created.session_id,
            "steerId": "s-injected",
            "prompt": [{"type": "text", "text": "allow running pytest"}],
        },
    )
    release_tool.set()
    await asyncio.wait_for(prompt_task2, timeout=5.0)
    assert "allow running pytest" in noted


@pytest.mark.asyncio
async def test_steer_cleared_when_turn_ends_with_error_and_not_leaked_to_next_prompt(
    tmp_path: Path,
):
    """Undelivered steer on an error-stopped turn is cleared and never leaks to the next prompt."""
    stream_started = asyncio.Event()
    fail_stream = asyncio.Event()
    second_call_user_texts: list[str] = []
    bg_tasks: list[asyncio.Task[Any]] = []
    call_count = 0

    async def stream_fn(model: Model, context: Any, options: Any = None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            stream = AssistantMessageEventStream()
            partial = _assistant_msg(model, [], stop_reason="error")
            stream.push(StartEvent(partial=partial.model_copy(deep=True)))

            async def _finish_with_error() -> None:
                stream_started.set()
                await fail_stream.wait()
                err_msg = _assistant_msg(model, [], stop_reason="error")
                err_msg.errorMessage = "upstream 500"
                stream.push(
                    ErrorEvent(partial=err_msg, reason="error", error_message="upstream 500")
                )
                stream.set_final_message(err_msg)
                stream.end()

            bg_tasks.append(asyncio.create_task(_finish_with_error()))
            return stream

        for m in context.messages:
            if getattr(m, "role", None) == "user":
                second_call_user_texts.append(
                    "".join(
                        b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                        for b in (m.content if isinstance(m.content, list) else [])
                    )
                )
        return _text_stream(model, "fresh answer")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))

    prompt_task = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("failing turn")])
    )
    await asyncio.wait_for(stream_started.wait(), timeout=5.0)

    steer_res = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": created.session_id,
            "steerId": "s-err-1",
            "prompt": [{"type": "text", "text": "stranded steer that must not leak"}],
        },
    )
    assert steer_res == {
        "sessionId": created.session_id,
        "steerId": "s-err-1",
        "queued": True,
        "queueLength": 1,
    }

    fail_stream.set()
    await asyncio.wait_for(prompt_task, timeout=5.0)
    await asyncio.gather(*bg_tasks)

    harness = agent._harnesses[created.session_id]
    assert harness.phase == "idle"
    assert harness.steer_queue == []

    cleared_notifs = [
        p
        for m, p in client.ext_notifications
        if m == "pi/queue_update" and p.get("clearedByAbort") is True
    ]
    assert cleared_notifs
    assert cleared_notifs[-1]["steer"] == []
    assert cleared_notifs[-1].get("clearedSteerIds") == ["s-err-1"]

    # A subsequent unrelated prompt must NOT receive the old steer.
    res2 = await agent.prompt(
        session_id=created.session_id, prompt=[text_block("new unrelated prompt")]
    )
    assert res2.stop_reason == "end_turn"
    assert all("stranded steer that must not leak" not in t for t in second_call_user_texts)
    assert any("new unrelated prompt" in t for t in second_call_user_texts)


@pytest.mark.asyncio
async def test_steer_rejected_after_cancel_while_turn_unwinding(tmp_path: Path):
    """Steer arriving after session/cancel is rejected with reason='aborting' and never leaks."""
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    second_call_user_texts: list[str] = []
    call_count = 0

    def ext(pi: Any) -> None:
        async def hold_tool(
            tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None
        ):
            tool_started.set()
            await release_tool.wait()
            return {"content": [{"type": "text", "text": "done"}]}

        pi.register_tool(
            ToolDefinition(
                name="cancel_hold_probe",
                description="waits",
                parameters={"type": "object", "properties": {}},
                execute=hold_tool,
                annotations=ToolAnnotations(readOnlyHint=True),
            )
        )

    async def stream_fn(model: Model, context: Any, options: Any = None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_stream(model, "call_cancel_hold", "cancel_hold_probe", {})
        for m in context.messages:
            if getattr(m, "role", None) == "user":
                second_call_user_texts.append(
                    "".join(
                        b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                        for b in (m.content if isinstance(m.content, list) else [])
                    )
                )
        return _text_stream(model, "clean next turn")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn, extensions=[ext])
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))

    prompt_task = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("turn to cancel")])
    )
    await asyncio.wait_for(tool_started.wait(), timeout=5.0)

    # Cancel and immediately send a steer BEFORE releasing the tool or awaiting abort tasks.
    await agent.cancel(session_id=created.session_id)
    steer_res = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": created.session_id,
            "steerId": "s-after-cancel",
            "prompt": [{"type": "text", "text": "late steer after cancel"}],
        },
    )
    assert steer_res == {
        "sessionId": created.session_id,
        "steerId": "s-after-cancel",
        "queued": False,
        "reason": "aborting",
    }

    release_tool.set()
    await asyncio.wait_for(prompt_task, timeout=5.0)
    if agent._abort_tasks:
        await asyncio.gather(*list(agent._abort_tasks), return_exceptions=True)

    harness = agent._harnesses[created.session_id]
    assert harness.phase == "idle"
    assert harness.steer_queue == []

    # Subsequent prompt must NOT see the rejected steer.
    res2 = await agent.prompt(
        session_id=created.session_id, prompt=[text_block("follow-up after cancel")]
    )
    assert res2.stop_reason == "end_turn"
    assert all("late steer after cancel" not in t for t in second_call_user_texts)
    assert any("follow-up after cancel" in t for t in second_call_user_texts)


# ---------------------------------------------------------------------------
# X10: pi/session/btw unit & integration tests
# ---------------------------------------------------------------------------


def test_repair_dangling_tool_calls_and_flatten_history():
    model = Model(provider="openai", model_id="gpt-4o")
    tc_done: ToolCallContent = {
        "type": "toolCall",
        "id": "call_1",
        "name": "read",
        "arguments": {"path": "README.md"},
    }
    tc_running: ToolCallContent = {
        "type": "toolCall",
        "id": "call_2",
        "name": "bash",
        "arguments": {"command": "pytest -v"},
    }
    messages = [
        UserMessage(content=[{"type": "text", "text": "check repo"}]),
        _assistant_msg(
            model,
            [
                {"type": "thinking", "thinking": "secret chain"},
                {"type": "text", "text": "Reading README"},
                tc_done,
            ],
            stop_reason="toolUse",
        ),
        ToolResultMessage(
            toolCallId="call_1",
            toolName="read",
            content=[{"type": "text", "text": "# hello"}],
            isError=False,
        ),
        _assistant_msg(model, [tc_running], stop_reason="toolUse"),
    ]

    repaired = repair_dangling_tool_calls(messages)
    assert repaired[-1].role == "toolResult"
    assert repaired[-1].toolCallId == "call_2"

    flat = flatten_tool_history_for_btw(messages, model=model)
    # Tool-free: only user/assistant roles and no toolCall/thinking blocks anywhere.
    for msg in flat:
        assert msg.role in ("user", "assistant")
        if isinstance(msg.content, list):
            for block in msg.content:
                btype = (
                    block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                )
                assert btype not in ("toolCall", "thinking")
    joined = "\n".join(
        b.get("text", "")
        for m in flat
        for b in (m.content if isinstance(m.content, list) else [])
        if isinstance(b, dict)
    )
    assert "pytest -v" in joined
    assert "still running in the main session" in joined


def test_parse_btw_history_bounds():
    turns = parse_btw_history([{"role": "user", "text": "q1"}, {"role": "assistant", "text": "a1"}])
    assert len(turns) == 2
    with pytest.raises(ValueError, match="maximum"):
        parse_btw_history([{"role": "user", "text": "x"}] * (BTW_MAX_HISTORY_TURNS * 2 + 1))
    with pytest.raises(ValueError, match="bytes"):
        parse_btw_history([{"role": "user", "text": "a" * (BTW_MAX_HISTORY_BYTES + 1)}])


@pytest.mark.asyncio
async def test_btw_blank_session_leaves_no_jsonl_on_disk(tmp_path: Path):
    """Invariant B1: /btw on a blank deferred session writes nothing to disk."""
    client = _RecordingClient()
    agent = _make_agent(tmp_path, lambda m, c, o=None: _text_stream(m, "quick answer"))
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))

    harness = agent._harnesses[created.session_id]
    storage = harness.session.get_storage()
    assert isinstance(storage, DeferredJsonlSessionStorage)
    assert storage.written is False

    res = await agent.ext_method(
        "pi/session/btw",
        {
            "sessionId": created.session_id,
            "btwId": "btw-blank",
            "prompt": [{"type": "text", "text": "what is exit 137?"}],
        },
    )
    assert res == {
        "sessionId": created.session_id,
        "btwId": "btw-blank",
        "stopReason": "end_turn",
        "error": None,
    }
    assert storage.written is False
    assert list((tmp_path / "sessions").glob("*.jsonl")) == []
    # Main session/update never received btw text.
    main_text_updates = [
        u for _, u in client.updates if getattr(u, "session_update", None) == "agent_message_chunk"
    ]
    assert main_text_updates == []
    btw_notifs = [p for m, p in client.ext_notifications if m == "pi/btw_update"]
    assert any(n["btwId"] == "btw-blank" and n["delta"] == "quick answer" for n in btw_notifs)
    assert btw_notifs[-1]["kind"] == "done"


@pytest.mark.asyncio
async def test_btw_runs_parallel_during_busy_tool_and_does_not_pollute_jsonl(tmp_path: Path):
    """Invariants B1, B2, B3, B4: /btw completes while main tool is blocked & JSONL is unchanged."""
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    btw_contexts: list[Any] = []
    executed_tools: list[str] = []

    def ext(pi: Any) -> None:
        async def blocker(
            tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None
        ):
            executed_tools.append("blocker")
            tool_started.set()
            await release_tool.wait()
            return {"content": [{"type": "text", "text": "blocker finished"}]}

        pi.register_tool(
            ToolDefinition(
                name="blocker",
                description="blocks main turn",
                parameters={"type": "object", "properties": {}},
                execute=blocker,
                annotations=ToolAnnotations(readOnlyHint=True),
            )
        )

    async def stream_fn(model: Model, context: Any, options: Any = None):
        # Detect btw call by tools == [] and <btw_side_question> marker.
        if context.tools == []:
            btw_contexts.append(context)
            # Also push a rogue ToolCallStartEvent to verify B3 ignores it completely.
            stream = AssistantMessageEventStream()
            final = _assistant_msg(model, [{"type": "text", "text": "side answer"}])
            stream.push(StartEvent(partial=final.model_copy(deep=True)))
            stream.push(ToolCallStartEvent(content_index=0, partial=final))
            stream.push(TextDeltaEvent(content_index=0, delta="side answer", partial=final))
            stream.push(DoneEvent(partial=final, reason="stop"))
            stream.set_final_message(final)
            stream.end()
            return stream
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return _text_stream(model, "main done")
        return _tool_call_stream(model, "call_blk", "blocker", {})

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn, extensions=[ext])
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))

    main_task = asyncio.create_task(
        agent.prompt(session_id=created.session_id, prompt=[text_block("run blocker")])
    )
    await asyncio.wait_for(tool_started.wait(), timeout=5.0)

    jsonl_files = list((tmp_path / "sessions").glob("*.jsonl"))
    assert len(jsonl_files) == 1
    before_bytes = jsonl_files[0].read_bytes()

    # Run /btw while main turn is still blocked inside blocker tool!
    btw_res = await asyncio.wait_for(
        agent.ext_method(
            "pi/session/btw",
            {
                "sessionId": created.session_id,
                "btwId": "btw-parallel",
                "prompt": [{"type": "text", "text": "what tool is running?"}],
            },
        ),
        timeout=5.0,
    )
    assert btw_res["stopReason"] == "end_turn"
    assert not main_task.done()  # Completed strictly before main turn unblocked (B2)!
    assert jsonl_files[0].read_bytes() == before_bytes  # Byte-for-byte unchanged (B1)!
    assert len(btw_contexts) == 1
    assert btw_contexts[0].tools == []
    assert executed_tools == ["blocker"]  # Rogue ToolCallStartEvent never executed (B3)!

    # Verify the dangling tool call was repaired & flattened into btw context (B4).
    ctx_text = "\n".join(
        b.get("text", "")
        for m in btw_contexts[0].messages
        for b in (m.content if isinstance(m.content, list) else [])
        if isinstance(b, dict)
    )
    assert "blocker" in ctx_text
    assert "still running in the main session" in ctx_text

    release_tool.set()
    await asyncio.wait_for(main_task, timeout=5.0)


@pytest.mark.asyncio
async def test_btw_cancel_and_concurrency_limit(tmp_path: Path):
    started_count = 0
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    bg_tasks: list[asyncio.Task[None]] = []

    async def slow_btw_stream(model: Model, context: Any, options: Any = None):
        nonlocal started_count
        stream = AssistantMessageEventStream()
        started_count += 1
        if started_count == 1:
            first_started.set()
        elif started_count >= 2:
            second_started.set()

        async def _drip():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass
            finally:
                stream.end()

        bg_tasks.append(asyncio.create_task(_drip()))
        return stream

    client = _RecordingClient()
    agent = _make_agent(tmp_path, slow_btw_stream)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))

    t1 = asyncio.create_task(
        agent.ext_method(
            "pi/session/btw",
            {
                "sessionId": created.session_id,
                "btwId": "b1",
                "prompt": [{"type": "text", "text": "q1"}],
            },
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=5.0)
    t2 = asyncio.create_task(
        agent.ext_method(
            "pi/session/btw",
            {
                "sessionId": created.session_id,
                "btwId": "b2",
                "prompt": [{"type": "text", "text": "q2"}],
            },
        )
    )
    await asyncio.wait_for(second_started.wait(), timeout=5.0)

    # Third concurrent btw on same session is rejected.
    with pytest.raises(RequestError):
        await agent.ext_method(
            "pi/session/btw",
            {
                "sessionId": created.session_id,
                "btwId": "b3",
                "prompt": [{"type": "text", "text": "q3"}],
            },
        )

    # Cancel b1 independently.
    cancel_res = await agent.ext_method(
        "pi/session/btw_cancel",
        {"sessionId": created.session_id, "btwId": "b1"},
    )
    assert cancel_res == {"sessionId": created.session_id, "btwId": "b1", "cancelled": True}
    r1 = await asyncio.wait_for(t1, timeout=5.0)
    assert r1["stopReason"] == "cancelled"

    # Closing session cancels remaining b2.
    await agent.close_session(session_id=created.session_id)
    r2 = await asyncio.wait_for(t2, timeout=5.0)
    assert r2["stopReason"] == "cancelled"


@pytest.mark.asyncio
async def test_btw_slot_reserved_before_snapshot_yields_for_limit_and_duplicate_id(
    tmp_path: Path,
):
    """Finding #3: parallel /btw on uncached session enforces limit=2 and duplicate btwId."""
    release_snapshot = asyncio.Event()
    started_streams: list[str] = []

    async def stream_fn(model: Model, context: Any, options: Any = None):
        started_streams.append("btw")
        return _text_stream(model, "ok")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))
    harness = agent._harnesses[created.session_id]

    orig_snapshot = harness.snapshot_context_for_btw

    async def slow_snapshot():
        await release_snapshot.wait()
        return await orig_snapshot()

    harness.snapshot_context_for_btw = slow_snapshot  # type: ignore[method-assign]

    # Fire 6 concurrent requests with distinct ids + 1 duplicate btwId while snapshot is suspended.
    coros = [
        agent.ext_method(
            "pi/session/btw",
            {
                "sessionId": created.session_id,
                "btwId": f"race-{i}",
                "prompt": [{"type": "text", "text": f"q{i}"}],
            },
        )
        for i in range(6)
    ]
    coros.append(
        agent.ext_method(
            "pi/session/btw",
            {
                "sessionId": created.session_id,
                "btwId": "race-0",
                "prompt": [{"type": "text", "text": "dup"}],
            },
        )
    )
    tasks = [asyncio.create_task(c) for c in coros]
    await asyncio.sleep(0)
    release_snapshot.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, RequestError)]
    assert len(succeeded) == 2
    assert len(failed) == 5
    assert len(started_streams) == 2
    assert isinstance(results[-1], RequestError)


@pytest.mark.asyncio
async def test_steer_waiting_for_trust_and_waiting_for_barriers(tmp_path: Path):
    """Finding #4 & #9: steer returns waiting_for_trust and waiting_for_barriers accurately."""
    barrier_entered = asyncio.Event()
    release_barrier = asyncio.Event()

    def ext(pi: Any) -> None:
        async def wait_barrier() -> None:
            barrier_entered.set()
            await release_barrier.wait()

        pi.add_turn_barrier(wait_barrier)

    client = _RecordingClient()
    agent = _make_agent(tmp_path, lambda m, c, o=None: _text_stream(m, "done"), extensions=[ext])
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))
    sid = created.session_id

    # 1. Simulate unsettled project trust -> waiting_for_trust.
    trust_state = agent._trust[sid]
    trust_state.settled.clear()
    res_trust = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": sid,
            "steerId": "s-trust",
            "prompt": [{"type": "text", "text": "steer during trust"}],
        },
    )
    assert res_trust == {
        "sessionId": sid,
        "steerId": "s-trust",
        "queued": False,
        "reason": "waiting_for_trust",
    }

    # 2. Settle trust; prompt blocks on turn barrier -> waiting_for_barriers!
    trust_state.settled.set()
    prompt_task = asyncio.create_task(agent.prompt(session_id=sid, prompt=[text_block("go")]))
    await asyncio.wait_for(barrier_entered.wait(), timeout=5.0)

    res_barrier = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": sid,
            "steerId": "s-barrier",
            "prompt": [{"type": "text", "text": "steer during barrier"}],
        },
    )
    assert res_barrier == {
        "sessionId": sid,
        "steerId": "s-barrier",
        "queued": False,
        "reason": "waiting_for_barriers",
    }

    release_barrier.set()
    prompt_res = await asyncio.wait_for(prompt_task, timeout=5.0)
    assert prompt_res.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_steer_invalid_params_unknown_session_and_image_block(tmp_path: Path):
    """Finding #9: invalid params, unknown session, and steer carrying an image content block."""
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    seen_image_blocks: list[dict[str, Any]] = []

    def ext(pi: Any) -> None:
        async def hold_tool(
            tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None
        ):
            tool_started.set()
            await release_tool.wait()
            return {"content": [{"type": "text", "text": "ok"}]}

        pi.register_tool(
            ToolDefinition(
                name="img_hold_probe",
                description="waits",
                parameters={"type": "object", "properties": {}},
                execute=hold_tool,
                annotations=ToolAnnotations(readOnlyHint=True),
            )
        )

    call_count = 0

    async def stream_fn(model: Model, context: Any, options: Any = None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_stream(model, "call_img", "img_hold_probe", {})
        for msg in context.messages:
            if getattr(msg, "role", None) == "user" and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        seen_image_blocks.append(block)
        return _text_stream(model, "saw image")

    client = _RecordingClient()
    agent = _make_agent(tmp_path, stream_fn, extensions=[ext])
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))
    sid = created.session_id

    # Unknown session -> RequestError
    with pytest.raises(RequestError):
        await agent.ext_method(
            "pi/session/steer",
            {"sessionId": "nonexistent-session", "prompt": [{"type": "text", "text": "hi"}]},
        )
    # Empty prompt -> RequestError
    with pytest.raises(RequestError):
        await agent.ext_method("pi/session/steer", {"sessionId": sid, "prompt": []})
    # Blank text only -> RequestError
    with pytest.raises(RequestError):
        await agent.ext_method(
            "pi/session/steer", {"sessionId": sid, "prompt": [{"type": "text", "text": "   "}]}
        )

    # Steer with an image content block while turn is active.
    prompt_task = asyncio.create_task(agent.prompt(session_id=sid, prompt=[text_block("check")]))
    await asyncio.wait_for(tool_started.wait(), timeout=5.0)

    steer_res = await agent.ext_method(
        "pi/session/steer",
        {
            "sessionId": sid,
            "steerId": "s-img",
            "prompt": [
                {"type": "text", "text": "look at this screenshot"},
                {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
            ],
        },
    )
    assert steer_res["queued"] is True
    release_tool.set()
    await asyncio.wait_for(prompt_task, timeout=5.0)
    assert len(seen_image_blocks) == 1
    assert seen_image_blocks[0]["mimeType"] == "image/png"
    assert seen_image_blocks[0]["data"] == "iVBORw0KGgo="


@pytest.mark.asyncio
async def test_btw_timeout_model_override_unknown_cancel_and_pending_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Finding #9: /btw timeout, [btw].model + key override, unknown btw_cancel, pending trust."""
    monkeypatch.setenv("TEST_MAIN_KEY", "main-secret")
    monkeypatch.setenv("TEST_BTW_KEY", "btw-secret")

    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "openai"\nid = "gpt-4o"\napi_key_env = "TEST_MAIN_KEY"\n\n'
        '[[models]]\nprovider = "openai"\nid = "gpt-4o-mini"\napi_key_env = "TEST_BTW_KEY"\n\n'
        '[btw]\nmodel = "gpt-4o-mini"\nthinking = "off"\ntimeout_seconds = 5\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)

    captured_calls: list[tuple[str, str | None]] = []

    async def stream_fn(model: Model, context: Any, options: Any = None):
        api_key = getattr(options, "api_key", None)
        captured_calls.append((model.model_id, api_key))
        return _text_stream(model, "override answer")

    client = _RecordingClient()
    agent = PiAcpAgent(stream_fn=stream_fn, home=tmp_path, config=cfg)
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path))
    sid = created.session_id

    # 1. Unknown btw_cancel is idempotent.
    cancel_unknown = await agent.ext_method(
        "pi/session/btw_cancel", {"sessionId": sid, "btwId": "never-existed"}
    )
    assert cancel_unknown == {"sessionId": sid, "btwId": "never-existed", "cancelled": True}

    # 2. [btw].model override uses configured model and api_key_env, even while trust is pending.
    agent._trust[sid].settled.clear()
    btw_res = await agent.ext_method(
        "pi/session/btw",
        {
            "sessionId": sid,
            "btwId": "btw-override",
            "prompt": [{"type": "text", "text": "quick check while trust pending"}],
        },
    )
    assert btw_res["stopReason"] == "end_turn"
    assert captured_calls == [("gpt-4o-mini", "btw-secret")]

    # 3. /btw timeout aborts stream signal and returns stopReason='error'.
    aborted_signal = False
    bg_tasks: list[asyncio.Task[None]] = []

    async def hanging_stream(model: Model, context: Any, options: Any = None):
        nonlocal aborted_signal
        stream = AssistantMessageEventStream()
        signal = getattr(options, "signal", None)

        async def _wait_and_check():
            nonlocal aborted_signal
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                pass
            finally:
                if signal is not None and getattr(signal, "aborted", False):
                    aborted_signal = True
                stream.end()

        bg_tasks.append(asyncio.create_task(_wait_and_check()))
        return stream

    from dataclasses import replace as dc_replace

    fast_timeout_cfg = dc_replace(cfg, btw=dc_replace(cfg.btw, timeout_seconds=0.05))
    agent_to = PiAcpAgent(stream_fn=hanging_stream, home=tmp_path / "to", config=fast_timeout_cfg)
    agent_to.on_connect(client)
    created_to = await agent_to.new_session(cwd=str(tmp_path))
    res_to = await agent_to.ext_method(
        "pi/session/btw",
        {
            "sessionId": created_to.session_id,
            "btwId": "btw-timeout",
            "prompt": [{"type": "text", "text": "hang forever"}],
        },
    )
    assert res_to["stopReason"] == "error"
    assert "timed out" in (res_to["error"] or "").lower()
    for t in bg_tasks:
        t.cancel()
    await asyncio.gather(*bg_tasks, return_exceptions=True)
    assert aborted_signal is True


@pytest.mark.asyncio
async def test_stdio_wire_capabilities_steer_and_btw(tmp_path: Path):
    """End-to-end stdio JSON-RPC contract for X9 (`pi/session/steer`) and X10 (`pi/session/btw`)."""
    import subprocess
    import sys

    from acp import PROTOCOL_VERSION, spawn_agent_process
    from acp.schema import DeniedOutcome, RequestPermissionResponse

    class _StdioClient:
        def __init__(self) -> None:
            self.ext_notifications: list[tuple[str, dict[str, Any]]] = []

        async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
            return None

        async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
            self.ext_notifications.append((method, params))

    client = _StdioClient()
    env = {"PI_HOME": str(tmp_path), "PI_USE_MOCK": "1"}
    async with spawn_agent_process(
        client,
        sys.executable,
        "-m",
        "pi_agent_cli",
        env=env,
        cwd=tmp_path,
        transport_kwargs={"stderr": subprocess.DEVNULL, "shutdown_timeout": 10.0},
    ) as (conn, _proc):
        init = await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), 30.0)
        meta = getattr(init, "field_meta", None) or {}
        assert meta.get("pi/capabilities") == {"steer": True, "btw": True}

        created = await asyncio.wait_for(conn.new_session(cwd=str(tmp_path), mcp_servers=[]), 30.0)
        sid = created.session_id

        # Idle steer returns queued=False, reason=idle over real stdio wire.
        steer_res = await asyncio.wait_for(
            conn.ext_method(
                "pi/session/steer",
                {"sessionId": sid, "steerId": "st-wire", "prompt": [{"type": "text", "text": "x"}]},
            ),
            30.0,
        )
        assert steer_res == {
            "sessionId": sid,
            "steerId": "st-wire",
            "queued": False,
            "reason": "idle",
        }

        # /btw streams pi/btw_update over real stdio wire and writes zero JSONL files.
        btw_res = await asyncio.wait_for(
            conn.ext_method(
                "pi/session/btw",
                {
                    "sessionId": sid,
                    "btwId": "btw-wire",
                    "prompt": [{"type": "text", "text": "quick question"}],
                },
            ),
            30.0,
        )
        assert btw_res["sessionId"] == sid
        assert btw_res["btwId"] == "btw-wire"
        assert btw_res["stopReason"] == "end_turn"
        assert btw_res["error"] is None

        btw_notifs = [p for m, p in client.ext_notifications if m == "pi/btw_update"]
        deltas = "".join(p["delta"] for p in btw_notifs if p.get("kind") == "text_delta")
        assert deltas == "Hello from mock"
        assert any(p.get("kind") == "done" for p in btw_notifs)
        assert list((tmp_path / "sessions").glob("*.jsonl")) == []
