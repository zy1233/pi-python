"""``provider = "openai"`` keeps the thinking that a gateway streams as ``reasoning_content``.

Found in the VS Code extension: its bundled agent has only the OpenAI-format provider (no
``langchain_deepseek``), so a DeepSeek model behind a gateway has to use ``provider = "openai"``,
and plain ``ChatOpenAI`` dropped ``delta.reasoning_content``. No thinking reached the client.
The sessions of the same model with ``provider = "deepseek"`` had thinking blocks; the sessions
with ``provider = "openai"`` had none.
"""

from __future__ import annotations

import importlib
import sys
import time
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

pytest.importorskip("langchain_openai")

from langchain_openai import ChatOpenAI

from pi_agent_core.adapters.langchain_convert import convert_to_langchain
from pi_agent_core.adapters.openai_reasoning import (
    keep_streamed_reasoning,
    reasoning_chat_openai,
)
from pi_agent_core.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import LlmContext, Model, StreamOptions

ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")

GATEWAY = "http://llm-gw.example.test/v1"


def gateway_model(*, reasoning: bool = True) -> Model:
    return Model(
        provider="openai", model_id="deepseek-flash", base_url=GATEWAY, reasoning=reasoning
    )


def now() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# keep_streamed_reasoning: one chunk
# ---------------------------------------------------------------------------


def generation_chunk(content: str = "") -> ChatGenerationChunk:
    return ChatGenerationChunk(message=AIMessageChunk(content=content))


def raw(delta: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


class TestOneChunk:
    def test_reasoning_content_is_kept(self):
        made = generation_chunk()

        keep_streamed_reasoning(raw({"reasoning_content": "let me think"}), made)

        assert made.message.additional_kwargs == {"reasoning_content": "let me think"}

    def test_reasoning_is_kept_as_reasoning_content(self):
        """OpenRouter, newer vLLM and Ollama call it ``reasoning`` (as ``ChatDeepSeek`` reads)."""
        made = generation_chunk()

        keep_streamed_reasoning(raw({"reasoning": "hmm"}), made)

        assert made.message.additional_kwargs == {"reasoning_content": "hmm"}

    def test_reasoning_content_wins_over_reasoning(self):
        made = generation_chunk()

        keep_streamed_reasoning(raw({"reasoning_content": "first", "reasoning": "second"}), made)

        assert made.message.additional_kwargs == {"reasoning_content": "first"}

    def test_an_empty_reasoning_content_falls_through_to_reasoning(self):
        made = generation_chunk()

        keep_streamed_reasoning(raw({"reasoning_content": "", "reasoning": "second"}), made)

        assert made.message.additional_kwargs == {"reasoning_content": "second"}

    @pytest.mark.parametrize("delta", [{}, {"content": "hi"}, {"reasoning_content": None}])
    def test_a_delta_without_thinking_is_not_touched(self, delta: dict[str, Any]):
        made = generation_chunk("hi")

        keep_streamed_reasoning(raw(delta), made)

        assert made.message.additional_kwargs == {}

    @pytest.mark.parametrize("value", [["a"], {"text": "a"}, 3, True])
    def test_a_thinking_that_is_not_text_is_ignored(self, value: Any):
        made = generation_chunk()

        keep_streamed_reasoning(raw({"reasoning_content": value}), made)

        assert made.message.additional_kwargs == {}

    @pytest.mark.parametrize(
        "chunk",
        [
            {},
            {"choices": []},
            {"choices": None},
            {"choices": ["not a dict"]},
            {"choices": [{"delta": None}]},
            {"choices": [{"index": 0}]},
            "not a dict",
            None,
        ],
    )
    def test_a_chunk_of_another_shape_is_left_alone(self, chunk: Any):
        """The usage-only chunk at the end of a stream has an empty ``choices`` list."""
        made = generation_chunk()

        keep_streamed_reasoning(chunk, made)

        assert made.message.additional_kwargs == {}

    def test_no_generation_chunk_is_fine(self):
        keep_streamed_reasoning(raw({"reasoning_content": "x"}), None)


# ---------------------------------------------------------------------------
# resolve_chat_model
# ---------------------------------------------------------------------------


class TestResolve:
    def test_the_openai_provider_is_a_chat_openai(self):
        chat = ls_mod.resolve_chat_model(gateway_model(), "sk-test", "high")

        assert isinstance(chat, ChatOpenAI)
        assert type(chat) is reasoning_chat_openai()

    def test_the_class_is_built_once(self):
        assert reasoning_chat_openai() is reasoning_chat_openai()

    def test_the_request_is_as_before(self):
        """Only the answer is changed: the reasoning effort, endpoint and stream flags stay."""
        chat = ls_mod.resolve_chat_model(gateway_model(), "sk-test", "high")

        params = chat._default_params

        assert params["reasoning_effort"] == "high"
        assert chat.openai_api_base == GATEWAY
        assert chat.stream_usage is True

    def test_a_missing_langchain_openai_is_named(self, monkeypatch: pytest.MonkeyPatch):
        reasoning_chat_openai.cache_clear()
        monkeypatch.setitem(sys.modules, "langchain_openai", None)  # an import of it raises
        try:
            with pytest.raises(ImportError, match="langchain-openai"):
                ls_mod.resolve_chat_model(gateway_model(), "sk-test")
        finally:
            reasoning_chat_openai.cache_clear()


# ---------------------------------------------------------------------------
# The whole chain: a streamed answer of a gateway, through the real chat class
# ---------------------------------------------------------------------------


class _Stream:
    """What ``async_client.create`` returns for a streamed completion."""

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class _FakeCompletions:
    """Stands in for ``ChatOpenAI.async_client``: records each request, streams canned replies."""

    def __init__(self, *replies: list[dict]) -> None:
        self.requests: list[dict] = []
        self._replies = list(replies)

    async def create(self, **payload: Any) -> _Stream:
        self.requests.append(payload)
        return _Stream(self._replies.pop(0))


TOOL_ROUND = [
    _chunk({"role": "assistant", "content": "", "reasoning_content": "I should "}),
    _chunk({"reasoning_content": "call echo"}),
    _chunk(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"m": "hi"}'},
                }
            ]
        }
    ),
    _chunk({}, finish="tool_calls"),
]
ANSWER_ROUND = [
    _chunk({"role": "assistant", "content": "", "reasoning_content": "all good"}),
    _chunk({"content": "hi there"}),
    _chunk({}, finish="stop"),
]
PLAIN_ROUND = [
    _chunk({"role": "assistant", "content": "hello"}),
    _chunk({"content": " world"}),
    _chunk({}, finish="stop"),
]


def echo_tool() -> SimpleTool:
    return SimpleTool(
        name="echo",
        description="echo",
        label="echo",
        parameters={"type": "object", "properties": {"m": {"type": "string"}}},
        execute_fn=lambda *a: None,
    )


def user(value: str) -> UserMessage:
    return UserMessage(content=value, timestamp=now())


def result(call_id: str = "call_1") -> ToolResultMessage:
    return ToolResultMessage(
        toolCallId=call_id,
        toolName="echo",
        content=[{"type": "text", "text": "hi"}],
        timestamp=now(),
    )


def wire_up(
    monkeypatch: pytest.MonkeyPatch, model: Model, *replies: list[dict]
) -> _FakeCompletions:
    """The adapter's own entry point, with the HTTP call of the real chat class faked."""
    fake = _FakeCompletions(*replies)
    chat = ls_mod.resolve_chat_model(model, "sk-test", "high")
    chat.async_client = fake
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: chat)
    return fake


async def run_turn(model: Model, messages: list[Any], *, tools: list[SimpleTool] | None = None):
    stream = await ls_mod.langchain_stream(
        model,
        LlmContext(system_prompt=None, messages=messages, tools=tools or []),
        StreamOptions(reasoning="high", api_key="sk-test"),
    )
    events = [event async for event in stream]
    return events, await stream.message_result()


@pytest.mark.asyncio
async def test_a_gateway_thinking_streams_as_thinking_events(monkeypatch: pytest.MonkeyPatch):
    """The failure that was seen: with ``provider = "openai"`` no ``thinking_delta`` came out."""
    model = gateway_model()
    wire_up(monkeypatch, model, TOOL_ROUND)

    events, final = await run_turn(model, [user("say hi")], tools=[echo_tool()])

    deltas = [e.delta for e in events if e.type == "thinking_delta"]
    assert deltas == ["I should ", "call echo"]
    assert final.content[0] == {"type": "thinking", "thinking": "I should call echo"}
    assert final.content[1]["type"] == "toolCall"


@pytest.mark.asyncio
async def test_the_thinking_comes_before_the_text(monkeypatch: pytest.MonkeyPatch):
    model = gateway_model()
    wire_up(monkeypatch, model, ANSWER_ROUND)

    events, final = await run_turn(model, [user("hi")])

    kinds = [e.type for e in events]
    assert kinds.index("thinking_end") < kinds.index("text_start")
    assert final.content == [
        {"type": "thinking", "thinking": "all good"},
        {"type": "text", "text": "hi there"},
    ]


@pytest.mark.asyncio
async def test_an_answer_without_thinking_has_none(monkeypatch: pytest.MonkeyPatch):
    """api.openai.com and every endpoint that sends no thinking: nothing changes."""
    model = gateway_model(reasoning=False)
    wire_up(monkeypatch, model, PLAIN_ROUND)

    events, final = await run_turn(model, [user("hi")])

    assert not [e for e in events if e.type.startswith("thinking")]
    assert final.content == [{"type": "text", "text": "hello world"}]


@pytest.mark.asyncio
async def test_a_tool_round_trip_does_not_send_the_thinking_back(
    monkeypatch: pytest.MonkeyPatch,
):
    """The thinking is kept for the client and for the session. It is not sent to the gateway.

    A gateway of the OpenAI format may refuse ``reasoning_content`` on a request, and the
    ``thinking`` content block is not a Chat Completions block. The second request must be the
    plain one that ``provider = "openai"`` has always sent.
    """
    model = gateway_model()
    fake = wire_up(monkeypatch, model, TOOL_ROUND, ANSWER_ROUND)
    tools = [echo_tool()]
    messages: list[Any] = [user("say hi")]

    _, first = await run_turn(model, messages, tools=tools)
    messages += [first, result()]
    _, second = await run_turn(model, messages, tools=tools)

    assert first.content[0] == {"type": "thinking", "thinking": "I should call echo"}
    assert second.content[0] == {"type": "thinking", "thinking": "all good"}
    wire = fake.requests[1]["messages"]
    assert [m["role"] for m in wire] == ["user", "assistant", "tool"]
    assistant = wire[1]
    assert "reasoning_content" not in assistant
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant.get("content") in (None, "", [])
    assert "thinking" not in str(assistant)


def test_a_history_with_thinking_makes_a_request_that_the_chat_class_accepts():
    """A session that holds thinking blocks (from the stream above) replays without a crash."""
    model = gateway_model()
    history = [
        user("say hi"),
        AssistantMessage(
            content=[
                {"type": "thinking", "thinking": "plan"},
                {"type": "text", "text": "calling"},
                {"type": "toolCall", "id": "call_1", "name": "echo", "arguments": {"m": "hi"}},
            ],
            provider="openai",
            model="deepseek-flash",
            timestamp=now(),
        ),
        result(),
    ]
    chat = ls_mod.resolve_chat_model(model, "sk-test", "high")

    payload = chat._get_request_payload(convert_to_langchain(history, model=model))

    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant["content"] == [{"type": "text", "text": "calling"}]
    assert "reasoning_content" not in assistant
