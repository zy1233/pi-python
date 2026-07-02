"""Tests for pi -> LangChain message conversion (audit fixes B7, C1/#9)."""

from __future__ import annotations

import time

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pi_agent_core.adapters.langchain_convert import convert_to_langchain
from pi_agent_core.messages import AssistantMessage, ToolResultMessage
from pi_agent_core.types import Model


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        provider="anthropic",
        model="claude-x",
        timestamp=int(time.time() * 1000),
    )


def test_assistant_without_thinking_keeps_string_content():
    msg = _assistant({"type": "text", "text": "hello"})
    (ai,) = convert_to_langchain([msg])
    assert isinstance(ai, AIMessage)
    assert ai.content == "hello"


def test_assistant_thinking_replayed_with_signature():
    """B7: thinking blocks (and signatures) must be replayed ahead of text/tool use."""
    msg = _assistant(
        {"type": "thinking", "thinking": "let me think", "signature": "sig-1"},
        {"type": "text", "text": "the answer"},
        {"type": "toolCall", "id": "toolu_1", "name": "echo", "arguments": {"m": "x"}},
    )
    (ai,) = convert_to_langchain([msg])

    assert isinstance(ai.content, list)
    assert ai.content[0] == {"type": "thinking", "thinking": "let me think", "signature": "sig-1"}
    assert ai.content[1] == {"type": "text", "text": "the answer"}
    assert ai.tool_calls[0]["id"] == "toolu_1"
    assert ai.tool_calls[0]["name"] == "echo"


def test_assistant_thinking_without_signature():
    msg = _assistant(
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "ok"},
    )
    (ai,) = convert_to_langchain([msg])
    assert ai.content[0] == {"type": "thinking", "thinking": "hmm"}
    assert "signature" not in ai.content[0]


_IMG = {"type": "image", "data": "aGk=", "mimeType": "image/png"}


def _tool_result(*blocks) -> ToolResultMessage:
    return ToolResultMessage(
        toolCallId="call_1",
        toolName="screenshot",
        content=list(blocks),
        timestamp=int(time.time() * 1000),
    )


def test_tool_result_image_anthropic_native_blocks():
    """C1/#9: Anthropic tool results carry images as native content blocks."""
    msg = _tool_result({"type": "text", "text": "captured"}, _IMG)
    model = Model(provider="anthropic", model_id="claude-x")
    (tool_msg,) = convert_to_langchain([msg], model=model)

    assert isinstance(tool_msg, ToolMessage)
    assert isinstance(tool_msg.content, list)
    assert tool_msg.content[0] == {"type": "text", "text": "captured"}
    assert tool_msg.content[1]["type"] == "image_url"
    assert tool_msg.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_tool_result_image_openai_user_message_fallback():
    """C1/#9: providers without tool-result images get a follow-up user message."""
    msg = _tool_result(_IMG)
    model = Model(provider="openai", model_id="gpt-x")
    out = convert_to_langchain([msg], model=model)

    assert len(out) == 2
    tool_msg, human = out
    assert isinstance(tool_msg, ToolMessage)
    assert isinstance(tool_msg.content, str)
    assert isinstance(human, HumanMessage)
    assert "call_1" in human.content[0]["text"]
    assert human.content[1]["type"] == "image_url"


def test_tool_result_image_stripped_without_image_support():
    """C1/#9: supports_images=False models get a text placeholder, no image parts."""
    msg = _tool_result({"type": "text", "text": "captured"}, _IMG)
    model = Model(provider="openai", model_id="gpt-x", supports_images=False)
    (tool_msg,) = convert_to_langchain([msg], model=model)

    assert isinstance(tool_msg.content, str)
    assert "captured" in tool_msg.content
    assert "[image content removed]" in tool_msg.content


def test_tool_result_text_only_unchanged():
    msg = _tool_result({"type": "text", "text": "plain"})
    (tool_msg,) = convert_to_langchain([msg], model=Model(provider="openai", model_id="g"))
    assert tool_msg.content == "plain"
