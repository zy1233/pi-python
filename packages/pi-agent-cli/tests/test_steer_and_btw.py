"""Tests for Pre-Refactor X9 (pi/session/steer) and X10 (pi/session/btw)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError
from acp.helpers import text_block

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
async def test_advertise_commands_includes_btw(tmp_path: Path):
    client = _RecordingClient()
    agent = _make_agent(tmp_path, lambda m, c, o=None: _text_stream(m, "ok"))
    agent.on_connect(client)
    await agent.new_session(cwd=str(tmp_path))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    command_names: set[str] = set()
    for _, update in client.updates:
        if getattr(update, "session_update", None) == "available_commands_update":
            command_names.update(c.name for c in update.available_commands)
    assert "btw" in command_names


def test_btw_config_parsing(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "openai"\nid = "gpt-4o"\n\n'
        '[btw]\nmodel = "gpt-4o-mini"\nthinking = "inherit"\ntimeout_seconds = 45\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.btw.model == "gpt-4o-mini"
    assert cfg.btw.thinking == "inherit"
    assert cfg.btw.timeout_seconds == 45.0
    assert cfg.btw.notes == ()


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
    btw_started = asyncio.Event()
    bg_tasks: list[asyncio.Task[None]] = []

    async def slow_btw_stream(model: Model, context: Any, options: Any = None):
        stream = AssistantMessageEventStream()
        btw_started.set()

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
    await asyncio.wait_for(btw_started.wait(), timeout=5.0)
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
    await asyncio.sleep(0.05)

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
