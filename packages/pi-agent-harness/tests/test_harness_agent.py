"""H2 tests for AgentHarness runtime, hooks, queues, and persistence."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions.types import ToolDefinition
from pi_agent_core.messages import UserMessage
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import (
    AgentToolResult,
    BeforeToolCallResult,
    DoneEvent,
    Model,
    StartEvent,
    StreamOptions,
)
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


async def _run_echo_with_tool_call_hooks(*hooks):
    """Run one echo tool call through ``hooks`` (registered in order).

    Returns ``(times the tool actually ran, the toolResult message)``.
    """

    class EchoParams(BaseModel):
        message: str = ""

    ran: list[str] = []

    async def echo(_id, params, signal, on_update):
        ran.append("echo")
        return AgentToolResult(content=[{"type": "text", "text": "raw"}], details={})

    tool = SimpleTool("echo", "", "Echo", EchoParams, echo)
    session = await _memory_session()
    harness = AgentHarness(
        session=session, model=_model(), stream_fn=_tool_once_stream, tools=[tool]
    )
    for hook in hooks:
        harness.on("tool_call", hook)
    await harness.prompt("use tool")

    context = await session.build_context()
    tool_results = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
    assert len(tool_results) == 1
    return len(ran), tool_results[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "later_verdict",
    [None, {}, {"block": False}, {"block": None}, {"reason": "looks fine"}],
    ids=["none", "empty-dict", "block-false", "block-none", "reason-only"],
)
async def test_tool_call_block_is_final_and_later_hooks_cannot_override_it(later_verdict):
    """pi's emitToolCall returns on the first ``block``.

    The ACP permission layer registers first and extensions after it; an extension
    must not be able to un-deny a call by returning a non-blocking verdict.
    """
    later_calls: list[str] = []

    def permission_layer(_event):
        return {"block": True, "reason": "denied by permission layer"}

    def later_extension(_event):
        later_calls.append("called")
        return later_verdict

    ran, result = await _run_echo_with_tool_call_hooks(permission_layer, later_extension)

    assert ran == 0
    assert result.isError is True
    assert "denied by permission layer" in result.content[0]["text"]
    assert later_calls == []  # dispatch stops at the first block, like pi


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "earlier_verdict",
    [None, {}, {"block": False}],
    ids=["none", "empty-dict", "block-false"],
)
async def test_tool_call_later_hook_can_still_block_after_non_blocking_hooks(earlier_verdict):
    ran, result = await _run_echo_with_tool_call_hooks(
        lambda _event: earlier_verdict,
        lambda _event: {"block": True, "reason": "blocked by extension"},
    )

    assert ran == 0
    assert result.isError is True
    assert "blocked by extension" in result.content[0]["text"]


@pytest.mark.asyncio
async def test_tool_call_block_verdict_may_be_an_object_and_is_still_final():
    ran, result = await _run_echo_with_tool_call_hooks(
        lambda _event: BeforeToolCallResult(block=True, reason="object verdict"),
        lambda _event: {},
    )

    assert ran == 0
    assert "object verdict" in result.content[0]["text"]


@pytest.mark.asyncio
async def test_tool_call_without_any_block_still_runs_the_tool():
    ran, result = await _run_echo_with_tool_call_hooks(
        lambda _event: None,
        lambda _event: {},
        lambda _event: {"block": False},
    )

    assert ran == 1
    assert result.isError is False


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


# ---------------------------------------------------------------------------
# check_tool_call: the tool_call policy chain, for calls made outside the loop
# ---------------------------------------------------------------------------
#
# Extensions that run agents of their own (dynamic workflows) do so on fresh harnesses
# that carry no hooks, so the session's permission layer never saw their tool calls.
# ``check_tool_call`` lets them put each call through *this* harness's chain.


async def _idle_harness(**kwargs) -> AgentHarness:
    return AgentHarness(
        session=await _memory_session("gate"),
        model=_model(),
        stream_fn=mock_text_stream,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_check_tool_call_allows_when_no_hook_blocks():
    harness = await _idle_harness()
    harness.on("tool_call", lambda _event: None)
    harness.on("tool_call", lambda _event: {})
    harness.on("tool_call", lambda _event: {"block": False})

    assert await harness.check_tool_call("c1", "bash", {"command": "ls"}) is None


@pytest.mark.asyncio
async def test_check_tool_call_allows_when_there_are_no_hooks():
    harness = await _idle_harness()

    assert await harness.check_tool_call("c1", "bash", {"command": "ls"}) is None


@pytest.mark.asyncio
async def test_check_tool_call_returns_the_first_block_and_stops():
    harness = await _idle_harness()
    later: list[str] = []
    harness.on("tool_call", lambda _event: {"block": True, "reason": "no bash here"})
    harness.on("tool_call", lambda _event: later.append("ran"))

    verdict = await harness.check_tool_call("c1", "bash", {"command": "ls"})

    assert verdict == {"block": True, "reason": "no bash here"}
    assert later == []


@pytest.mark.asyncio
async def test_check_tool_call_normalizes_object_verdicts_to_a_dict():
    harness = await _idle_harness()
    harness.on("tool_call", lambda _event: BeforeToolCallResult(block=True, reason="object"))

    assert await harness.check_tool_call("c1", "bash", {}) == {"block": True, "reason": "object"}


@pytest.mark.asyncio
async def test_check_tool_call_hands_hooks_the_call_and_where_it_came_from():
    harness = await _idle_harness()
    seen: list = []
    harness.on("tool_call", seen.append)

    origin = {"kind": "subagent", "cwd": "/work"}
    await harness.check_tool_call("c1", "write", {"path": "a.txt"}, origin=origin)
    await harness.check_tool_call("c2", "read", {"path": "b.txt"})

    first, second = seen
    assert (first.toolCallId, first.toolName, first.input) == ("c1", "write", {"path": "a.txt"})
    assert first.origin == origin
    assert second.origin is None  # the session's own calls carry no origin


@pytest.mark.asyncio
async def test_check_tool_call_hook_errors_normalize_to_hook_code():
    """A policy backend that fails must not read as "allowed"."""
    harness = await _idle_harness()

    def broken_policy(_event):
        raise RuntimeError("policy backend down")

    harness.on("tool_call", broken_policy)

    with pytest.raises(AgentHarnessError) as excinfo:
        await harness.check_tool_call("c1", "bash", {})

    assert excinfo.value.code == "hook"


@pytest.mark.asyncio
async def test_extension_bridge_exposes_the_tool_call_gate():
    """An extension reaches the session's tool_call policy through the bridge."""
    captured: dict = {}

    def extension(pi):
        captured["gate"] = pi._require_bridge().tool_call_gate

    harness = await _idle_harness(extensions=[extension])
    harness.on(
        "tool_call",
        lambda event: {"block": True, "reason": "policy"} if event.toolName == "bash" else None,
    )
    await harness.load_extensions()

    gate = captured["gate"]
    assert await gate("c1", "bash", {"command": "ls"}) == {"block": True, "reason": "policy"}
    assert await gate("c2", "read", {"path": "a.txt"}) is None
    origin = {"kind": "subagent", "cwd": "/w"}
    assert await gate("c3", "bash", {}, origin=origin) == {"block": True, "reason": "policy"}


# ---------------------------------------------------------------------------
# Tool annotations reach the tool_call hooks
# ---------------------------------------------------------------------------
#
# The CLI's permission layer asks about a call unless the tool says it only reads. A hook
# is handed the call's name and input; the harness adds what the registered tool declares
# about itself, whichever way the call arrives (its own loop, or ``check_tool_call`` for the
# sub-agents of a workflow).


class _PeekParams(BaseModel):
    message: str = ""


def _peek_tool(**kwargs) -> SimpleTool:
    async def execute(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "peeked"}], details={})

    return SimpleTool("echo", "", "Echo", _PeekParams, execute, **kwargs)


@pytest.mark.asyncio
async def test_a_tool_call_hook_sees_what_the_called_tool_declares():
    tool = _peek_tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    harness = AgentHarness(
        session=await _memory_session(),
        model=_model(),
        stream_fn=_tool_once_stream,
        tools=[tool],
    )
    seen: list = []
    harness.on("tool_call", seen.append)

    await harness.prompt("use tool")

    (event,) = seen
    assert event.annotations == {"readOnlyHint": True, "openWorldHint": False}


@pytest.mark.asyncio
async def test_a_tool_that_declares_nothing_has_no_annotations_on_the_event():
    harness = AgentHarness(
        session=await _memory_session(),
        model=_model(),
        stream_fn=_tool_once_stream,
        tools=[_peek_tool()],
    )
    seen: list = []
    harness.on("tool_call", seen.append)

    await harness.prompt("use tool")

    (event,) = seen
    assert event.annotations is None


@pytest.mark.asyncio
async def test_check_tool_call_looks_the_annotations_up_by_tool_name():
    harness = await _idle_harness(tools=[_peek_tool(annotations={"readOnlyHint": True})])
    seen: list = []
    harness.on("tool_call", seen.append)

    origin = {"kind": "subagent", "cwd": "/w"}
    await harness.check_tool_call("c1", "echo", {}, origin=origin)
    await harness.check_tool_call("c2", "mystery", {}, origin=origin)

    known, unknown = seen
    assert known.annotations == {"readOnlyHint": True}
    assert unknown.annotations is None  # not a tool of this session: nothing to take at its word


@pytest.mark.asyncio
async def test_annotations_that_are_not_a_mapping_count_as_none():
    harness = await _idle_harness(tools=[_peek_tool(annotations=["readOnlyHint"])])
    seen: list = []
    harness.on("tool_call", seen.append)

    await harness.check_tool_call("c1", "echo", {})

    assert seen[0].annotations is None


@pytest.mark.asyncio
async def test_a_hook_cannot_rewrite_what_a_tool_declares():
    tool = _peek_tool(annotations={"readOnlyHint": True})
    harness = await _idle_harness(tools=[tool])

    def tamper(event):
        event.annotations["readOnlyHint"] = False

    harness.on("tool_call", tamper)
    await harness.check_tool_call("c1", "echo", {})

    assert tool.annotations == {"readOnlyHint": True}


@pytest.mark.asyncio
async def test_an_extension_tool_keeps_the_annotations_it_was_registered_with():
    async def execute(_id, params, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": "peeked"}], details={})

    def extension(pi):
        pi.register_tool(
            ToolDefinition(
                name="peek",
                description="",
                parameters=_PeekParams,
                execute=execute,
                annotations={"readOnlyHint": True},
            )
        )
        pi.register_tool(
            ToolDefinition(name="poke", description="", parameters=_PeekParams, execute=execute)
        )

    harness = await _idle_harness(extensions=[extension])
    await harness.load_extensions()
    seen: list = []
    harness.on("tool_call", seen.append)

    await harness.check_tool_call("c1", "peek", {})
    await harness.check_tool_call("c2", "poke", {})

    peek, poke = seen
    assert peek.annotations == {"readOnlyHint": True}
    assert poke.annotations is None


# ---------------------------------------------------------------------------
# Project-local extensions are opt-in (audit P7-02)
# ---------------------------------------------------------------------------
#
# ``<cwd>/.pi-python/extensions`` ships with the repository and is imported (= executed)
# when the first prompt loads extensions. With auto-discovery on, that directory is only
# scanned when the harness is told to trust the project.


def _project_with_extension(tmp_path, monkeypatch, name: str):
    """A project whose extension leaves ``marker`` behind when it is *imported*."""
    from pathlib import Path

    from pi_agent_core.extensions import ExtensionLoader

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])

    project = tmp_path / "project"
    extensions = project / ".pi-python" / "extensions"
    extensions.mkdir(parents=True)
    marker = tmp_path / "imported.marker"
    (extensions / f"{name}.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def activate(pi):\n"
        f"    pi.register_command({name!r}, description='ext', handler=lambda args: None)\n",
        encoding="utf-8",
    )
    return project, marker


async def _discovering_harness(project, **kwargs) -> AgentHarness:
    from pi_agent_harness.env import LocalExecutionEnv

    return AgentHarness(
        session=await _memory_session("discover"),
        model=_model(),
        stream_fn=mock_text_stream,
        env=LocalExecutionEnv(str(project)),
        auto_discover_extensions=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_auto_discovery_skips_untrusted_project_extensions(tmp_path, monkeypatch):
    project, marker = _project_with_extension(tmp_path, monkeypatch, "harness_untrusted")
    harness = await _discovering_harness(project)

    await harness.load_extensions()

    assert not marker.exists()  # never imported
    assert "harness_untrusted" not in harness.extension_registry.get_commands()
    ((skipped),) = harness.skipped_extensions
    assert skipped.names == ("harness_untrusted.py",)
    assert skipped.directory == project / ".pi-python" / "extensions"


@pytest.mark.asyncio
async def test_auto_discovery_loads_project_extensions_once_trusted(tmp_path, monkeypatch):
    project, marker = _project_with_extension(tmp_path, monkeypatch, "harness_trusted")
    harness = await _discovering_harness(project, trust_project_extensions=True)

    await harness.load_extensions()

    assert marker.exists()
    assert "harness_trusted" in harness.extension_registry.get_commands()
    assert harness.skipped_extensions == []


@pytest.mark.asyncio
async def test_skipped_extensions_is_empty_until_extensions_are_loaded():
    harness = await _idle_harness()

    assert harness.skipped_extensions == []


# ---------------------------------------------------------------------------
# An extension that fails to load is reported, not lost (audit P7-12)
# ---------------------------------------------------------------------------


def _raising_extension(pi) -> None:
    raise RuntimeError("cannot start")


@pytest.mark.asyncio
async def test_failed_extensions_lists_extensions_that_could_not_start_and_keeps_the_rest():
    def fine(pi) -> None:
        pi.register_command("still-here", description="x", handler=lambda args: None)

    harness = await _idle_harness(extensions=[_raising_extension, fine])

    assert harness.failed_extensions == []  # nothing is known until they are loaded
    await harness.load_extensions()

    ((failure),) = harness.failed_extensions
    assert failure.name.endswith("_raising_extension")
    assert failure.source == "programmatic"
    assert failure.error == "RuntimeError: cannot start"
    assert "still-here" in harness.extension_registry.get_commands()


@pytest.mark.asyncio
async def test_failed_extensions_includes_an_extension_dir_whose_module_cannot_be_imported(
    tmp_path,
):
    """``extension_dirs`` are scanned by the harness itself, ahead of ``load_all``."""
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "typo.py").write_text("import no_such_module_anywhere\n", encoding="utf-8")
    (extra / "fine.py").write_text(
        "def activate(pi):\n"
        "    pi.register_command('from-dir', description='x', handler=lambda args: None)\n",
        encoding="utf-8",
    )
    harness = await _idle_harness(extension_dirs=[str(extra)])

    await harness.load_extensions()

    ((failure),) = harness.failed_extensions
    assert (failure.name, failure.source) == ("typo.py", "directory")
    assert "no_such_module_anywhere" in failure.error
    assert "from-dir" in harness.extension_registry.get_commands()
