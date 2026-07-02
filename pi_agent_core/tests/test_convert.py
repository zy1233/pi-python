"""Tests for pi -> LangChain message conversion (audit fix B7)."""

from __future__ import annotations

import time

from langchain_core.messages import AIMessage

from pi_agent_core.adapters.langchain_convert import convert_to_langchain
from pi_agent_core.messages import AssistantMessage


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
