"""Tests for cross-provider transform_messages."""

from __future__ import annotations

import time

from pi_agent_core.messages import AssistantMessage, ToolResultMessage, UserMessage
from pi_agent_core.transform import (
    downgrade_thinking,
    normalize_tool_call_ids,
    strip_unsupported_images,
    transform_messages,
)
from pi_agent_core.types import Model


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        provider="openai",
        model="gpt-4",
        timestamp=int(time.time() * 1000),
    )


def _tool_result(tool_call_id: str) -> ToolResultMessage:
    return ToolResultMessage(
        toolCallId=tool_call_id,
        toolName="echo",
        content=[{"type": "text", "text": "ok"}],
        timestamp=int(time.time() * 1000),
    )


def test_normalize_openai_to_anthropic():
    anthropic_model = Model(provider="anthropic", model_id="claude-3")
    messages = [
        _assistant({"type": "toolCall", "id": "call_abc", "name": "echo", "arguments": {}}),
        _tool_result("call_abc"),
    ]
    result = normalize_tool_call_ids(messages, anthropic_model)
    assert result[0].content[0]["id"] == "toolu_abc"
    assert result[1].toolCallId == "toolu_abc"


def test_normalize_anthropic_to_openai():
    openai_model = Model(provider="openai", model_id="gpt-4")
    messages = [
        _assistant({"type": "toolCall", "id": "toolu_xyz", "name": "echo", "arguments": {}}),
        _tool_result("toolu_xyz"),
    ]
    result = normalize_tool_call_ids(messages, openai_model)
    assert result[0].content[0]["id"] == "call_xyz"
    assert result[1].toolCallId == "call_xyz"


def test_normalize_compatible_id_unchanged():
    model = Model(provider="openai", model_id="gpt-4")
    messages = [
        _assistant({"type": "toolCall", "id": "call_keep", "name": "echo", "arguments": {}}),
        _tool_result("call_keep"),
    ]
    result = normalize_tool_call_ids(messages, model)
    assert result[0].content[0]["id"] == "call_keep"
    assert result[1].toolCallId == "call_keep"


def test_downgrade_thinking_removes_blocks():
    model = Model(provider="openai", model_id="gpt-4", reasoning=False)
    messages = [
        _assistant(
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "hi"},
        )
    ]
    result = downgrade_thinking(messages, model)
    assert len(result[0].content) == 1
    assert result[0].content[0]["type"] == "text"


def test_downgrade_thinking_keeps_when_supported():
    model = Model(provider="anthropic", model_id="claude-3", reasoning=True)
    messages = [
        _assistant(
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "hi"},
        )
    ]
    result = downgrade_thinking(messages, model)
    assert len(result[0].content) == 2


def test_strip_unsupported_images():
    model = Model(provider="mock", model_id="text-only", supports_images=False)
    messages = [
        UserMessage(
            content=[
                {"type": "image", "data": "abc", "mimeType": "image/png"},
            ],
            timestamp=int(time.time() * 1000),
        )
    ]
    result = strip_unsupported_images(messages, model)
    assert result[0].content == [{"type": "text", "text": "[image content removed]"}]


def test_transform_messages_end_to_end():
    target = Model(
        provider="anthropic",
        model_id="claude-3",
        reasoning=False,
        supports_images=False,
    )
    messages = [
        UserMessage(
            content=[
                {"type": "text", "text": "look"},
                {"type": "image", "data": "x", "mimeType": "image/png"},
            ],
            timestamp=int(time.time() * 1000),
        ),
        _assistant(
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "answer"},
            {"type": "toolCall", "id": "call_99", "name": "echo", "arguments": {}},
        ),
        _tool_result("call_99"),
    ]
    result = transform_messages(messages, target)
    assert result[0].content == [{"type": "text", "text": "look"}]
    assert all(b.get("type") != "thinking" for b in result[1].content)
    assert result[1].content[-1]["id"] == "toolu_99"
    assert result[2].toolCallId == "toolu_99"
