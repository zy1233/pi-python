"""DeepSeek's thinking mode wants its ``reasoning_content`` back (audit P6-03, follow-up 4).

In thinking mode, a request that carries tools must send the ``reasoning_content`` of the earlier
assistant messages back, or DeepSeek answers 400 ("The `reasoning_content` in the thinking mode
must be passed back to the API"). The adapter already captures it as a ``thinking`` block;
``ChatDeepSeek`` takes it in but never sends it out, so thinking together with tools died after
the first tool round.

Pi's own OpenAI-compatible provider does the same for DeepSeek: the thinking goes back as
``reasoning_content``, and an assistant message without any gets an empty one.

Only DeepSeek's own API, and only while the adapter has asked for thinking, is touched: a gateway
that serves DeepSeek models has its own idea of both and may refuse the field.
"""

from __future__ import annotations

import importlib
import time
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pi_agent_core.adapters.deepseek_replay import apply_reasoning_replay, reasoning_text
from pi_agent_core.adapters.langchain_convert import convert_to_langchain
from pi_agent_core.messages import AssistantMessage, ToolResultMessage, UserMessage
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import LlmContext, Model, StreamOptions

ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")

SILICONFLOW = "https://api.siliconflow.cn/v1"


def deepseek(*, reasoning: bool = True, base_url: str | None = None) -> Model:
    return Model(
        provider="deepseek", model_id="deepseek-v4-flash", reasoning=reasoning, base_url=base_url
    )


def now() -> int:
    return int(time.time() * 1000)


def assistant(*blocks: dict, provider: str = "deepseek") -> AssistantMessage:
    return AssistantMessage(content=list(blocks), provider=provider, model="m", timestamp=now())


def thinking(text: str) -> dict:
    return {"type": "thinking", "thinking": text}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def call(call_id: str = "call_1") -> dict:
    return {"type": "toolCall", "id": call_id, "name": "echo", "arguments": {"m": "hi"}}


def result(call_id: str = "call_1") -> ToolResultMessage:
    return ToolResultMessage(
        toolCallId=call_id,
        toolName="echo",
        content=[{"type": "text", "text": "hi"}],
        timestamp=now(),
    )


def user(value: str) -> UserMessage:
    return UserMessage(content=value, timestamp=now())


class TestTheThinkingRidesAlong:
    """``convert_to_langchain`` keeps a DeepSeek turn's thinking where the chat model finds it."""

    def test_the_thinking_of_a_deepseek_turn_is_kept(self):
        message = assistant(thinking("let me think"), text("ok"))

        (ai,) = convert_to_langchain([message], model=deepseek())

        assert ai.additional_kwargs == {"reasoning_content": "let me think"}

    def test_the_content_keeps_its_shape(self):
        message = assistant(thinking("let me think"), text("ok"))

        (ai,) = convert_to_langchain([message], model=deepseek())

        assert ai.content == [thinking("let me think"), text("ok")]

    def test_blocks_are_joined_with_a_newline_and_blank_ones_dropped(self):
        message = assistant(thinking("first"), thinking("   "), thinking(""), thinking("second"))

        (ai,) = convert_to_langchain([message], model=deepseek())

        assert ai.additional_kwargs["reasoning_content"] == "first\nsecond"

    def test_what_is_not_a_block_is_skipped(self):
        assert reasoning_text(["junk", None, 3, thinking("x"), text("y")]) == "x"

    def test_a_turn_without_thinking_has_nothing_to_keep(self):
        (ai,) = convert_to_langchain([assistant(text("ok"))], model=deepseek())

        assert ai.additional_kwargs == {}

    def test_only_blank_thinking_has_nothing_to_keep(self):
        (ai,) = convert_to_langchain([assistant(thinking("  \n "), text("ok"))], model=deepseek())

        assert ai.additional_kwargs == {}

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "mock"])
    def test_other_providers_are_left_alone(self, provider: str):
        model = Model(provider=provider, model_id="m", reasoning=True)

        (ai,) = convert_to_langchain([assistant(thinking("hmm"), text("ok"))], model=model)

        assert ai.additional_kwargs == {}

    def test_without_a_model_nothing_is_added(self):
        (ai,) = convert_to_langchain([assistant(thinking("hmm"), text("ok"))])

        assert ai.additional_kwargs == {}


class TestWireMessages:
    """``apply_reasoning_replay`` edits the request's messages in place."""

    @staticmethod
    def history() -> tuple[list[Any], list[dict[str, Any]]]:
        messages = [
            HumanMessage("q"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "plan"},
                tool_calls=[{"id": "c1", "name": "echo", "args": {}}],
            ),
            ToolMessage(content="r", tool_call_id="c1"),
            AIMessage(content="done", additional_kwargs={"reasoning_content": "wrap up"}),
        ]
        wire = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "content": "r", "tool_call_id": "c1"},
            {"role": "assistant", "content": "done"},
        ]
        return messages, wire

    def test_assistant_messages_get_their_own_thinking(self):
        messages, wire = self.history()

        assert apply_reasoning_replay(messages, wire) is True

        assert wire[1]["reasoning_content"] == "plan"
        assert wire[3]["reasoning_content"] == "wrap up"

    def test_other_roles_are_left_alone(self):
        messages, wire = self.history()
        user_before, tool_before = dict(wire[0]), dict(wire[2])

        apply_reasoning_replay(messages, wire)

        assert wire[0] == user_before
        assert wire[2] == tool_before

    def test_an_assistant_message_without_thinking_gets_an_empty_one(self):
        messages, wire = self.history()
        messages[3] = AIMessage(content="done")

        apply_reasoning_replay(messages, wire)

        assert wire[3]["reasoning_content"] == ""

    def test_a_tool_call_message_gets_a_string_content(self):
        messages, wire = self.history()

        apply_reasoning_replay(messages, wire)

        assert wire[1]["content"] == ""

    def test_a_tool_call_message_keeps_the_text_it_has(self):
        messages, wire = self.history()
        wire[1]["content"] = "let me look"

        apply_reasoning_replay(messages, wire)

        assert wire[1]["content"] == "let me look"

    def test_a_message_without_tool_calls_keeps_a_missing_content(self):
        messages, wire = self.history()
        wire[3]["content"] = None

        apply_reasoning_replay(messages, wire)

        assert wire[3]["content"] is None

    def test_messages_that_do_not_line_up_are_not_touched(self):
        messages, wire = self.history()
        before = [dict(entry) for entry in wire]

        assert apply_reasoning_replay(messages[:-1], wire) is False

        assert wire == before


def history_for(model: Model) -> list[Any]:
    """user, assistant (thinks, calls a tool), tool result, assistant (thinks, answers), user."""
    return convert_to_langchain(
        [
            user("q1"),
            assistant(thinking("plan"), call()),
            result(),
            assistant(thinking("wrap up"), text("done")),
            user("q2"),
        ],
        model=model,
    )


class TestWhatTheChatModelSends:
    """The request ``ChatDeepSeek`` builds, from the same entry point the adapter uses."""

    @pytest.fixture(autouse=True)
    def _langchain_deepseek(self, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("langchain_deepseek")
        monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)

    @staticmethod
    def assistants(model: Model, level: str | None) -> list[dict]:
        chat = ls_mod.resolve_chat_model(model, "sk-test", level)
        payload = chat._get_request_payload(history_for(model))
        return [m for m in payload["messages"] if m["role"] == "assistant"]

    def test_thinking_goes_back_on_every_assistant_message(self):
        first, second = self.assistants(deepseek(), "high")

        assert first["reasoning_content"] == "plan"
        assert second["reasoning_content"] == "wrap up"

    def test_a_tool_call_message_has_a_string_content(self):
        first, _ = self.assistants(deepseek(), "high")

        assert first["content"] == ""
        assert first["tool_calls"][0]["id"] == "call_1"

    def test_the_text_of_an_answer_is_still_plain_text(self):
        _, second = self.assistants(deepseek(), "high")

        assert second["content"] == "done"

    @pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
    def test_every_thinking_level_sends_it(self, level: str):
        first, _ = self.assistants(deepseek(), level)

        assert first["reasoning_content"] == "plan"

    @pytest.mark.parametrize("level", ["off", None])
    def test_nothing_is_sent_when_thinking_is_off(self, level: str | None):
        for message in self.assistants(deepseek(), level):
            assert "reasoning_content" not in message

    def test_nothing_is_sent_for_a_model_that_cannot_reason(self):
        for message in self.assistants(deepseek(reasoning=False), "high"):
            assert "reasoning_content" not in message

    def test_a_gateway_gets_nothing(self):
        for message in self.assistants(deepseek(base_url=SILICONFLOW), "high"):
            assert "reasoning_content" not in message

    def test_a_deepseek_address_counts_as_the_official_api(self):
        model = deepseek(base_url="https://api.deepseek.com/v1")

        first, _ = self.assistants(model, "high")

        assert first["reasoning_content"] == "plan"

    def test_the_rest_of_the_request_is_as_langchain_built_it(self):
        chat = ls_mod.resolve_chat_model(deepseek(), "sk-test", "high")

        payload = chat._get_request_payload([HumanMessage("hi")], stop=["END"], stream=True)

        assert payload["stop"] == ["END"]
        assert payload["stream"] is True
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        assert payload["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_bound_tools_still_reach_the_request(self):
        """``bind_tools`` hands the schemas to the request as keyword arguments."""
        tool = {
            "type": "function",
            "function": {"name": "echo", "description": "d", "parameters": {"type": "object"}},
        }
        chat = ls_mod.resolve_chat_model(deepseek(), "sk-test", "high")

        payload = chat._get_request_payload([HumanMessage("hi")], tools=[tool])

        assert [t["function"]["name"] for t in payload["tools"]] == ["echo"]


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
        "model": "deepseek-v4-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class _FakeCompletions:
    """Stands in for ``ChatDeepSeek.async_client``: records each request, streams canned replies."""

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


@pytest.mark.asyncio
async def test_a_tool_round_trip_sends_the_thinking_back(monkeypatch: pytest.MonkeyPatch):
    """The stream captures the thinking, the next request carries it: the whole chain."""
    pytest.importorskip("langchain_deepseek")
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    model = deepseek()
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND)
    chat = ls_mod.resolve_chat_model(model, "sk-test", "high")
    chat.async_client = fake
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: chat)
    tool = SimpleTool(
        name="echo",
        description="echo",
        label="echo",
        parameters={"type": "object", "properties": {"m": {"type": "string"}}},
        execute_fn=lambda *a: None,
    )
    options = StreamOptions(reasoning="high", api_key="sk-test")
    messages: list[Any] = [user("say hi")]

    first_stream = await ls_mod.langchain_stream(
        model, LlmContext(system_prompt=None, messages=messages, tools=[tool]), options
    )
    _ = [event async for event in first_stream]
    first = await first_stream.message_result()
    messages += [first, result()]
    second_stream = await ls_mod.langchain_stream(
        model, LlmContext(system_prompt=None, messages=messages, tools=[tool]), options
    )
    _ = [event async for event in second_stream]

    assert first.content[0] == thinking("I should call echo")
    wire = fake.requests[1]["messages"]
    assert [m["role"] for m in wire] == ["user", "assistant", "tool"]
    assert wire[1]["reasoning_content"] == "I should call echo"
    assert wire[1]["content"] == ""
    assert wire[1]["tool_calls"][0]["id"] == "call_1"
    assert fake.requests[1]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert [t["function"]["name"] for t in fake.requests[1]["tools"]] == ["echo"]
    assert fake.requests[1]["stream"] is True
