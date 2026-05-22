"""Mock StreamFn for tests (no API keys)."""

from __future__ import annotations

import time
from typing import Any

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import AssistantMessage, ToolCallContent, Usage
from pi_agent_core.types import (
    DoneEvent,
    ErrorEvent,
    LlmContext,
    Model,
    StartEvent,
    StreamOptions,
    TextDeltaEvent,
)


def _base_partial(model: Model, content: list | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=content or [],
        api=model.api,
        provider=model.provider,
        model=model.model_id,
        usage=Usage(),
        stopReason="stop",
        timestamp=int(time.time() * 1000),
    )


async def mock_text_stream(
    model: Model,
    context: LlmContext,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    text = "Hello from mock"
    partial = _base_partial(model, [{"type": "text", "text": ""}])

    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    partial.content = [{"type": "text", "text": text}]
    stream.push(TextDeltaEvent(partial=partial.model_copy(deep=True), delta=text))
    partial.stopReason = "stop"
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="stop"))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def mock_tool_stream(
    model: Model,
    context: LlmContext,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    # After tool results are in context, return a final text response (end the loop)
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)

    stream = AssistantMessageEventStream()
    tc: ToolCallContent = {
        "type": "toolCall",
        "id": "call_1",
        "name": "echo",
        "arguments": {"message": "hi"},
    }
    partial = _base_partial(model, [tc])
    partial.stopReason = "toolUse"

    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def mock_error_stream(
    model: Model,
    context: LlmContext,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    partial = _base_partial(model)
    partial.stopReason = "error"
    partial.errorMessage = "mock error"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(ErrorEvent(partial=partial.model_copy(deep=True), reason="error", error_message="mock error"))
    stream.set_final_message(partial)
    stream.end()
    return stream
