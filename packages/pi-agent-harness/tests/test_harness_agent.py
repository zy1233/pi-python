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


async def _memory_session(session_id: str = "harness") -> Session:
    return Session(await MemorySessionStorage.create(cwd="/workspace", session_id=session_id))


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
            m.content for m in context.messages if getattr(m, "role", None) == "user"
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
        seen.extend([m.content for m in context.messages if getattr(m, "role", None) == "user"])
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
