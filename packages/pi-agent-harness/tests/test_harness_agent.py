"""H2 tests for AgentHarness runtime, hooks, queues, and persistence."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import UserMessage
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult, DoneEvent, Model, StartEvent, StreamOptions
from pi_agent_harness import AgentHarness, AgentHarnessError, MemorySessionStorage, Session
from pi_agent_harness.messages import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    harness_convert_to_llm,
)


def _model(model_id: str = "m1") -> Model:
    return Model(provider="mock", model_id=model_id)


def _user_text(message: UserMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if block.get("type") == "text")
    return str(content)


async def _memory_session(session_id: str = "harness") -> Session:
    return Session(await MemorySessionStorage.create(session_id=session_id))


@pytest.mark.asyncio
async def test_harness_prompt_persists_messages_and_emits_save_point_settled():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)
    events: list[str] = []

    async def listener(event, signal=None):
        events.append(event.type)
        if event.type == "message_end":
            entries = await session.get_entries()
            assert entries[-1].type == "message"

    harness.subscribe(listener)
    result = await harness.prompt("hello")

    assert result.role == "assistant"
    assert events[-2:] == ["agent_end", "settled"]
    assert "save_point" in events
    context = await session.build_context()
    assert [m.role for m in context.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_before_agent_start_and_context_hooks_can_add_and_replace_messages():
    session = await _memory_session()
    seen: dict[str, list[str]] = {}

    async def recording_stream(model, context, options=None):
        seen["messages"] = [
            _user_text(m) for m in context.messages if getattr(m, "role", None) == "user"
        ]
        return await mock_text_stream(model, context, options)

    harness = AgentHarness(session=session, model=_model(), stream_fn=recording_stream)

    def before_start(event):
        return {
            "messages": [UserMessage(content="extra", timestamp=2)],
            "system_prompt": "hooked",
        }

    def context_hook(event):
        messages = list(event.messages)
        messages.append(UserMessage(content="context", timestamp=3))
        return {"messages": messages}

    harness.on("before_agent_start", before_start)
    harness.on("context", context_hook)
    await harness.prompt("hello")

    assert seen["messages"] == ["hello", "extra", "context"]


@pytest.mark.asyncio
async def test_tool_hooks_block_or_patch_results():
    class EchoParams(BaseModel):
        message: str = ""

    async def echo(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "raw"}], details={})

    tool = SimpleTool("echo", "", "Echo", EchoParams, echo)
    session = await _memory_session()
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=_tool_once_stream,
        tools=[tool],
    )

    calls: list[str] = []

    def tool_call(event):
        calls.append(f"call:{event.toolName}")
        return None

    def tool_result(event):
        calls.append(f"result:{event.toolName}:{event.content[0]['text']}")
        return {"content": [{"type": "text", "text": "patched"}], "details": {"ok": True}}

    harness.on("tool_call", tool_call)
    harness.on("tool_result", tool_result)
    await harness.prompt("use tool")

    context = await session.build_context()
    tool_results = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
    assert calls == ["call:echo", "result:echo:raw"]
    assert tool_results[0].content == [{"type": "text", "text": "patched"}]
    assert tool_results[0].details == {"ok": True}


@pytest.mark.asyncio
async def test_next_turn_messages_are_injected_before_prompt():
    session = await _memory_session()
    seen: list[str] = []

    async def recording_stream(model, context, options=None):
        seen.extend([_user_text(m) for m in context.messages if getattr(m, "role", None) == "user"])
        return await mock_text_stream(model, context, options)

    harness = AgentHarness(session=session, model=_model(), stream_fn=recording_stream)
    await harness.next_turn("queued")
    await harness.prompt("prompt")

    assert seen[:2] == ["queued", "prompt"]


@pytest.mark.asyncio
async def test_set_model_during_turn_is_persisted_and_applies_next_turn():
    class EchoParams(BaseModel):
        message: str = ""

    async def echo(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    seen_models: list[str] = []

    async def two_turn_stream(model, context, options=None):
        seen_models.append(model.model_id)
        if not any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await _tool_once_stream(model, context, options)
        return await mock_text_stream(model, context, options)

    session = await _memory_session()
    harness = AgentHarness(
        session=session,
        model=_model("first"),
        stream_fn=two_turn_stream,
        tools=[SimpleTool("echo", "", "Echo", EchoParams, echo)],
    )

    async def change_model(event):
        await harness.set_model(_model("second"))

    harness.on("tool_call", change_model)
    await harness.prompt("go")

    assert seen_models == ["first", "second"]
    context = await session.build_context()
    assert context.model == {"provider": "mock", "modelId": "second"}


@pytest.mark.asyncio
async def test_run_failure_is_reported_as_closed_event_stream_and_persisted():
    async def exploding_stream(model, context, options=None):
        raise RuntimeError("boom")

    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=exploding_stream)
    events: list[str] = []
    harness.subscribe(lambda event, signal=None: events.append(event.type))

    result = await harness.prompt("go")

    assert result.stopReason == "error"
    assert result.errorMessage == "boom"
    assert "message_end" in events
    assert events[-3:] == ["save_point", "agent_end", "settled"]
    assert (await session.build_context()).messages[-1].stopReason == "error"


@pytest.mark.asyncio
async def test_hook_errors_normalize_to_hook_code():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)

    def boom(event):
        raise RuntimeError("hook boom")

    harness.on("before_agent_start", boom)
    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.prompt("hi")

    assert exc_info.value.code == "hook"
    assert harness.phase == "idle"


@pytest.mark.asyncio
async def test_subscriber_errors_normalize_to_hook_code():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)

    def listener(event, signal=None):
        if event.type == "queue_update":
            raise RuntimeError("listener boom")

    harness.subscribe(listener)
    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.next_turn("x")

    assert exc_info.value.code == "hook"


@pytest.mark.asyncio
async def test_provider_request_and_payload_hooks_chain_across_handlers():
    session = await _memory_session()
    seen: dict = {}

    async def recording_stream(model, context, options=None):
        seen["max_retries"] = options.max_retries
        seen["retry_max_delay"] = options.retry_max_delay
        seen["payload"] = await options.on_payload({"step": 0})
        return await mock_text_stream(model, context, options)

    harness = AgentHarness(session=session, model=_model(), stream_fn=recording_stream)
    second_handler_snapshots: list[int | None] = []

    harness.on("before_provider_request", lambda e: {"streamOptions": {"maxRetries": 9}})

    def second_request_handler(event):
        # Chained semantics: this handler must see the first handler's patch.
        second_handler_snapshots.append(event.streamOptions.maxRetries)
        return {"streamOptions": {"maxRetryDelayMs": 5000}}

    harness.on("before_provider_request", second_request_handler)
    harness.on("before_provider_payload", lambda e: {"payload": {"step": e.payload["step"] + 1}})
    harness.on("before_provider_payload", lambda e: {"payload": {"step": e.payload["step"] + 1}})

    await harness.prompt("hi")

    assert second_handler_snapshots == [9]
    assert seen["max_retries"] == 9
    assert seen["retry_max_delay"] == 5.0
    assert seen["payload"] == {"step": 2}


def test_harness_convert_to_llm_maps_custom_roles():
    messages = [
        BashExecutionMessage(command="echo hi", output="hi", timestamp=1),
        CustomMessage(customType="note", content="remember", display=True, timestamp=2),
        BranchSummaryMessage(summary="branch", fromId="x", timestamp=3),
        CompactionSummaryMessage(summary="compact", tokensBefore=10, timestamp=4),
    ]

    converted = harness_convert_to_llm(messages)

    assert [m.role for m in converted] == ["user", "user", "user", "user"]
    assert "Ran `echo hi`" in converted[0].content[0]["text"]
    assert converted[1].content == [{"type": "text", "text": "remember"}]
    assert "summary of a branch" in converted[2].content[0]["text"]
    assert "compacted" in converted[3].content[0]["text"]


def test_agent_message_protocol_accepts_any_role_carrier():
    from pi_agent_core.messages import AssistantMessage, ToolResultMessage
    from pi_agent_harness import AgentMessageProtocol

    class DeployNote(BaseModel):
        role: str = "deployNote"
        environment: str

    satisfying = [
        BashExecutionMessage(command="x", timestamp=1),
        CustomMessage(customType="n", content="c", display=True, timestamp=2),
        BranchSummaryMessage(summary="s", fromId="f", timestamp=3),
        CompactionSummaryMessage(summary="s", tokensBefore=1, timestamp=4),
        UserMessage(content="hi"),
        AssistantMessage(content=[{"type": "text", "text": "hi"}]),
        ToolResultMessage(toolCallId="1", toolName="t", content=[]),
        DeployNote(environment="staging"),
    ]
    assert all(isinstance(m, AgentMessageProtocol) for m in satisfying)

    class NoRole(BaseModel):
        text: str = ""

    assert not isinstance(NoRole(), AgentMessageProtocol)


@pytest.mark.asyncio
async def test_harness_convert_to_llm_handles_session_replayed_dicts():
    # Session replay keeps harness/unknown roles as raw dicts (design §3.2);
    # conversion must not silently drop them after a round-trip.
    session = await _memory_session()
    await session.append_message(
        BashExecutionMessage(command="pytest -q", output="ok", exitCode=0, timestamp=1)
    )
    await session.append_message({"role": "user", "content": "hello", "timestamp": 2})
    await session.append_message({"role": "unknownRole", "content": "x", "timestamp": 3})

    replayed = (await session.build_context()).messages
    assert isinstance(replayed[0], dict)

    converted = harness_convert_to_llm(replayed)

    assert [m.role for m in converted] == ["user", "user"]
    assert "Ran `pytest -q`" in converted[0].content[0]["text"]
    assert converted[1].content == "hello"


async def _tool_once_stream(
    model: Model,
    context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)
    stream = AssistantMessageEventStream()
    partial = _base_partial(
        model,
        [{"type": "toolCall", "id": "call_1", "name": "echo", "arguments": {"message": "hi"}}],
    )
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


@pytest.mark.asyncio
async def test_next_turn_queue_rolls_back_when_queue_update_fails():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)

    def listener(event, signal=None):
        if event.type == "queue_update" and harness.phase == "turn" and not event.nextTurn:
            raise RuntimeError("queue boom")

    harness.subscribe(listener)
    await harness.next_turn("queued")

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.prompt("prompt")

    assert exc_info.value.code == "hook"
    assert [_user_text(m) for m in harness.next_turn_queue] == ["queued"]


@pytest.mark.asyncio
async def test_steer_and_follow_up_reject_idle_enqueue():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.steer("x")
    assert exc_info.value.code == "invalid_state"

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.follow_up("x")
    assert exc_info.value.code == "invalid_state"


@pytest.mark.asyncio
async def test_steer_queue_drains_and_rolls_back_on_emit_failure():
    import asyncio

    class EchoParams(BaseModel):
        message: str = ""

    async def echo(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    session = await _memory_session()
    seen: list[str] = []
    steer_enqueued = False
    ready = asyncio.Event()

    async def recording_stream(model, context, options=None):
        seen.extend([_user_text(m) for m in context.messages if getattr(m, "role", None) == "user"])
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)
        ready.set()
        await asyncio.sleep(0.05)
        return await _tool_once_stream(model, context, options)

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=recording_stream,
        tools=[SimpleTool("echo", "", "Echo", EchoParams, echo)],
    )

    def listener(event, signal=None):
        nonlocal steer_enqueued
        if event.type == "queue_update" and event.steer:
            steer_enqueued = True
        if event.type == "queue_update" and steer_enqueued and not event.steer:
            raise RuntimeError("steer drain boom")

    harness.subscribe(listener)
    run_task = asyncio.create_task(harness.prompt("go"))
    await ready.wait()
    await harness.steer("steered")
    result = await run_task

    assert result.stopReason == "error"
    assert "steer drain boom" in (result.errorMessage or "")
    assert [_user_text(m) for m in harness.steer_queue] == ["steered"]
    assert "steered" not in seen


@pytest.mark.asyncio
async def test_tool_call_hook_can_block_execution():
    class EchoParams(BaseModel):
        message: str = ""

    async def echo(_id, params, signal, on_update):
        raise AssertionError("tool should not run")

    tool = SimpleTool("echo", "", "Echo", EchoParams, echo)
    session = await _memory_session()
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=_tool_once_stream,
        tools=[tool],
    )
    harness.on("tool_call", lambda _event: {"block": True, "reason": "blocked"})
    await harness.prompt("use tool")

    context = await session.build_context()
    tool_results = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
    assert len(tool_results) == 1
    assert tool_results[0].isError is True
    assert "blocked" in tool_results[0].content[0]["text"]


@pytest.mark.asyncio
async def test_abort_clears_queues_and_emits_abort_event():
    import asyncio

    class EchoParams(BaseModel):
        message: str = ""

    async def echo(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    started = asyncio.Event()

    async def slow_two_turn_stream(model, context, options=None):
        if not any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            started.set()
            await asyncio.sleep(0.05)
            return await _tool_once_stream(model, context, options)
        return await mock_text_stream(model, context, options)

    session = await _memory_session()
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=slow_two_turn_stream,
        tools=[SimpleTool("echo", "", "Echo", EchoParams, echo)],
    )
    events: list[str] = []
    harness.subscribe(lambda event, signal=None: events.append(event.type))

    run_task = asyncio.create_task(harness.prompt("go"))
    await started.wait()
    await harness.steer("steer-me")
    await harness.follow_up("follow-me")
    cleared = await harness.abort()
    await run_task

    assert [_user_text(m) for m in cleared["cleared_steer"]] == ["steer-me"]
    assert [_user_text(m) for m in cleared["cleared_follow_up"]] == ["follow-me"]
    assert events.count("abort") == 1
    assert harness.steer_queue == []
    assert harness.follow_up_queue == []


@pytest.mark.asyncio
async def test_abort_aggregates_hook_errors_from_multiple_steps():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)

    def listener(event, signal=None):
        if event.type == "queue_update":
            raise RuntimeError("queue boom")
        if event.type == "abort":
            raise RuntimeError("abort boom")

    harness.subscribe(listener)
    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.abort()

    assert exc_info.value.code == "hook"
    cause = exc_info.value.__cause__
    assert isinstance(cause, ExceptionGroup)
    assert len(cause.exceptions) == 2


@pytest.mark.asyncio
async def test_turn_end_broadcast_failure_still_flushes_pending_writes():
    class EchoParams(BaseModel):
        message: str = ""

    async def echo(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "ok"}], details={})

    session = await _memory_session()
    harness = AgentHarness(
        session=session,
        model=_model("first"),
        stream_fn=_tool_once_stream,
        tools=[SimpleTool("echo", "", "Echo", EchoParams, echo)],
    )

    async def change_model(_event):
        await harness.set_model(_model("second"))

    harness.on("tool_call", change_model)

    def listener(event, signal=None):
        if event.type == "turn_end":
            raise RuntimeError("turn_end boom")

    harness.subscribe(listener)
    with pytest.raises(AgentHarnessError):
        await harness.prompt("go")

    model_changes = [e for e in await session.get_entries() if e.type == "model_change"]
    assert len(model_changes) == 1
    assert model_changes[0].modelId == "second"


@pytest.mark.asyncio
async def test_double_run_failure_raises_unknown_with_both_causes():
    async def exploding_stream(model, context, options=None):
        raise RuntimeError("boom")

    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=exploding_stream)

    def listener(event, signal=None):
        if event.type == "message_end" and getattr(event.message, "stopReason", None) == "error":
            raise RuntimeError("persist boom")

    harness.subscribe(listener)
    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.prompt("go")

    assert exc_info.value.code == "unknown"
    cause = exc_info.value.__cause__
    assert isinstance(cause, ExceptionGroup)
    assert len(cause.exceptions) == 2
    assert str(cause.exceptions[0]) == "boom"
    assert str(cause.exceptions[1]) == "persist boom"


@pytest.mark.asyncio
async def test_wait_for_idle_blocks_until_run_completes():
    import asyncio

    ready = asyncio.Event()

    async def slow_stream(model, context, options=None):
        ready.set()
        await asyncio.sleep(0.05)
        return await mock_text_stream(model, context, options)

    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=slow_stream)
    run_task = asyncio.create_task(harness.prompt("hi"))
    await ready.wait()
    assert harness.phase == "turn"

    wait_task = asyncio.create_task(harness.wait_for_idle())
    await asyncio.sleep(0)
    assert not wait_task.done()

    await run_task
    await wait_task
    assert harness.phase == "idle"


@pytest.mark.asyncio
async def test_prompt_persists_user_message_as_text_block_array():
    session = await _memory_session()
    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)
    await harness.prompt("hello")

    user_entries = [
        e
        for e in await session.get_entries()
        if e.type == "message" and e.message.get("role") == "user"
    ]
    assert user_entries[0].message["content"] == [{"type": "text", "text": "hello"}]


@pytest.mark.asyncio
async def test_system_prompt_cached_across_turns_and_invalidated():
    session = await _memory_session()
    call_count = 0

    def get_sys_prompt(ctx):
        nonlocal call_count
        call_count += 1
        return f"System prompt call {call_count}"

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=mock_text_stream,
        system_prompt=get_sys_prompt,
    )

    # First turn
    await harness.prompt("turn 1")
    assert call_count == 1

    # Second turn (no change in tools/resources/model) -> cached
    await harness.prompt("turn 2")
    assert call_count == 1

    # Invalidate cache manually
    harness.invalidate_system_prompt_cache()
    await harness.prompt("turn 3")
    assert call_count == 2

    # Change model -> invalidates cache
    await harness.set_model(_model("m2"))
    await harness.prompt("turn 4")
    assert call_count == 3


# ---------------------------------------------------------------------------
# P7-01: Extension lifecycle events reach hooks
# ---------------------------------------------------------------------------


async def test_extension_lifecycle_events_reach_hooks():
    """Extension on('turn_end') / on('agent_end') handlers fire during prompt."""
    fired: list[str] = []

    def tracking_ext(pi):
        pi.on("turn_start", lambda e: fired.append("turn_start"))
        pi.on("turn_end", lambda e: fired.append("turn_end"))
        pi.on("agent_end", lambda e: fired.append("agent_end"))
        pi.on("message_start", lambda e: fired.append("message_start"))
        pi.on("message_end", lambda e: fired.append("message_end"))

    session = await _memory_session("ext-events")
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=mock_text_stream,
        extensions=[tracking_ext],
    )
    await harness.prompt("test")
    await harness.wait_for_idle()
    assert "turn_start" in fired
    assert "turn_end" in fired
    assert "agent_end" in fired
    assert "message_start" in fired
    assert "message_end" in fired


# ---------------------------------------------------------------------------
# P7-03: Bridge is available during activate
# ---------------------------------------------------------------------------


async def test_extension_bridge_available_during_activate():
    """pi.cwd is accessible during activate() (bridge bound before activate)."""
    captured_cwd: list[str] = []

    def cwd_ext(pi):
        captured_cwd.append(pi.cwd)

    session = await _memory_session("ext-cwd")
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=mock_text_stream,
        extensions=[cwd_ext],
    )
    await harness.prompt("test")
    assert len(captured_cwd) == 1
    assert isinstance(captured_cwd[0], str)


# ---------------------------------------------------------------------------
# P7-02: Failed activate rolls back in harness integration
# ---------------------------------------------------------------------------


async def test_extension_failed_activate_no_residual_tools():
    """A crashing extension must not leave ghost tools in the harness."""

    async def ghost_execute(tool_call_id, params, **kw):
        from pi_agent_core.types import AgentToolResult

        return AgentToolResult(content=[{"type": "text", "text": "ghost"}])

    from pi_agent_core.extensions import ToolDefinition

    def ghost_ext(pi):
        pi.register_tool(
            ToolDefinition(
                name="ghost_tool",
                description="Should not survive",
                parameters=None,
                execute=ghost_execute,
            )
        )
        raise RuntimeError("activate crash")

    session = await _memory_session("ext-ghost")
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=mock_text_stream,
        extensions=[ghost_ext],
    )
    await harness.prompt("test")
    assert "ghost_tool" not in harness._tools
    assert "ghost_tool" not in harness.extension_registry.get_tools()


# ---------------------------------------------------------------------------
# P7-04: session_id populated after first prompt
# ---------------------------------------------------------------------------


async def test_extension_session_id_populated():
    """session_id is available during activate() AND at turn_end."""
    activate_sid: list[str] = []
    hook_sid: list[str] = []

    def sid_ext(pi):
        activate_sid.append(pi.session_id)
        pi.on("turn_end", lambda _e: hook_sid.append(pi.session_id))

    session = await _memory_session("ext-sid")
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=mock_text_stream,
        extensions=[sid_ext],
    )
    await harness.prompt("test")
    await harness.wait_for_idle()
    # activate phase should already have session_id
    assert len(activate_sid) == 1
    assert activate_sid[0] != "", "session_id must be non-empty during activate"
    # turn_end hook should also have it
    assert len(hook_sid) == 1
    assert hook_sid[0] == activate_sid[0]


# ---------------------------------------------------------------------------
# Slash command dispatch (P7-07 fix)
# ---------------------------------------------------------------------------


class TestSlashCommandDispatch:
    """Verify slash command routing: /command args → extension handler."""

    @staticmethod
    def _greeting_ext(pi):
        """Extension that registers /greet and /info commands."""
        pi.register_command(
            "greet",
            description="Greet someone",
            handler=lambda args: pi.send_message(f"Hello, {args.strip() or 'world'}!"),
        )
        pi.register_command(
            "info",
            description="Show info",
            handler=lambda _args: pi.send_message("This is pi-python."),
        )

    @pytest.mark.asyncio
    async def test_dispatch_command_returns_output(self):
        """dispatch_command() returns captured send_message text."""
        session = await _memory_session("cmd-dispatch")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[self._greeting_ext],
        )
        await harness.load_extensions()

        result = await harness.dispatch_command("greet", "Alice")
        assert result == "Hello, Alice!"
        # steer_queue should be drained (not left for LLM)
        assert len(harness.steer_queue) == 0

    @pytest.mark.asyncio
    async def test_dispatch_command_unknown_returns_none(self):
        """dispatch_command() returns None for unregistered commands."""
        session = await _memory_session("cmd-unknown")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[self._greeting_ext],
        )
        await harness.load_extensions()

        result = await harness.dispatch_command("nonexistent", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_prompt_slash_skips_llm(self):
        """prompt('/greet Bob') dispatches to handler, never calls LLM."""
        llm_called = []

        async def tracking_stream(model, context, options=None):
            llm_called.append(True)
            return await mock_text_stream(model, context, options)

        session = await _memory_session("cmd-prompt")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=tracking_stream,
            extensions=[self._greeting_ext],
        )
        result = await harness.prompt("/greet Bob")
        await harness.wait_for_idle()

        # Handler output should be the assistant message
        assert result.role == "assistant"
        text = result.content[0]["text"]
        assert "Hello, Bob!" in text
        # LLM should NOT have been called
        assert len(llm_called) == 0

    @pytest.mark.asyncio
    async def test_prompt_slash_emits_full_event_sequence(self):
        """Slash dispatch emits agent_start…agent_end for subscribers."""
        events: list[str] = []

        session = await _memory_session("cmd-events")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[self._greeting_ext],
        )
        harness.subscribe(lambda e, s=None: events.append(e.type))
        await harness.prompt("/info")
        await harness.wait_for_idle()

        assert "agent_start" in events
        assert "turn_start" in events
        assert "message_update" in events
        assert "agent_end" in events
        assert "settled" in events

    @pytest.mark.asyncio
    async def test_prompt_slash_persists_messages(self):
        """Both user and assistant messages are persisted to the session."""
        session = await _memory_session("cmd-persist")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[self._greeting_ext],
        )
        await harness.prompt("/greet")
        await harness.wait_for_idle()

        context = await session.build_context()
        roles = [m.role for m in context.messages]
        assert roles == ["user", "assistant"]
        # User message should contain the original slash text
        user_text = _user_text(context.messages[0])
        assert user_text == "/greet"

    @pytest.mark.asyncio
    async def test_unrecognized_slash_falls_through_to_llm(self):
        """'/unknown' is not a registered command — passed through to LLM."""
        llm_called = []

        async def tracking_stream(model, context, options=None):
            llm_called.append(True)
            return await mock_text_stream(model, context, options)

        session = await _memory_session("cmd-fallthrough")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=tracking_stream,
            extensions=[self._greeting_ext],
        )
        await harness.prompt("/unknown stuff")
        await harness.wait_for_idle()
        assert len(llm_called) == 1

    @pytest.mark.asyncio
    async def test_dispatch_handler_error_returns_error_text(self):
        """Handler exception is captured and returned as error text."""

        def bad_ext(pi):
            def bad_handler(args):
                raise ValueError("boom")

            pi.register_command("fail", description="Always fails", handler=bad_handler)

        session = await _memory_session("cmd-error")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[bad_ext],
        )
        await harness.load_extensions()

        result = await harness.dispatch_command("fail", "")
        assert result is not None
        assert "Error executing /fail" in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_dispatch_drains_steer_queue(self):
        """Handler's send_message() calls are captured, not left in steer_queue."""

        def multi_msg_ext(pi):
            def handler(args):
                pi.send_message("line 1")
                pi.send_message("line 2")

            pi.register_command("multi", description="Multiple messages", handler=handler)

        session = await _memory_session("cmd-drain")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[multi_msg_ext],
        )
        await harness.load_extensions()

        result = await harness.dispatch_command("multi", "")
        assert result == "line 1\nline 2"
        assert len(harness.steer_queue) == 0

    @pytest.mark.asyncio
    async def test_load_extensions_idempotent(self):
        """Multiple load_extensions() calls do not double-register."""
        session = await _memory_session("cmd-idempotent")
        harness = AgentHarness(
            session=session,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[self._greeting_ext],
        )
        await harness.load_extensions()
        await harness.load_extensions()
        assert harness.extension_registry.command_count == 2  # greet + info
