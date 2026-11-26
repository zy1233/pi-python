"""``replay_reasoning``: send the thinking back to a gateway of the OpenAI format (opt-in).

Found in a long session of the VS Code extension: the model was DeepSeek behind a gateway, with
``provider = "openai"``. The adapter kept the streamed ``reasoning_content`` for the client but sent
none of it back, so in each turn the model worked out the same conclusions again (29 large thinking
turns, 79 % of 39 minutes). ``provider = "deepseek"`` on DeepSeek's own API sends it back
(``test_deepseek_replay.py``). A gateway gets it only when the user asks
(``Model.replay_reasoning``), because the OpenAI API and some gateways refuse an unknown field of
a message. A gateway that refuses it is noticed, and the request is made again without it.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

pytest.importorskip("langchain_openai")

import openai

from pi_agent_core.adapters.langchain_convert import convert_to_langchain
from pi_agent_core.adapters.openai_reasoning import reasoning_chat_openai
from pi_agent_core.messages import AssistantMessage, ToolResultMessage, UserMessage
from pi_agent_core.tests.fake_gateway import GATEWAY_URL, api_error, wire_up
from pi_agent_core.tests.fake_gateway import Stream as _Stream
from pi_agent_core.tests.fake_gateway import chunk as _chunk
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import LlmContext, Model, StreamOptions

ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")

GATEWAY = GATEWAY_URL
SILICONFLOW = "https://api.siliconflow.cn/v1"
DEEPSEEK_API = "https://api.deepseek.com/v1"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    """The refusals that were seen are kept for the life of the process: not between tests."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    ls_mod._REPLAY_REFUSED.clear()
    yield
    ls_mod._REPLAY_REFUSED.clear()


def gateway(
    *,
    provider: str = "openai",
    base_url: str | None = GATEWAY,
    model_id: str = "deepseek-flash",
    reasoning: bool = True,
    replay: bool = True,
) -> Model:
    return Model(
        provider=provider,
        model_id=model_id,
        base_url=base_url,
        reasoning=reasoning,
        replay_reasoning=replay,
    )


def own_api(**changes: Any) -> Model:
    """DeepSeek's own API: no ``base_url``, and no opt-in (it needs none)."""
    return gateway(provider="deepseek", base_url=None, replay=False, **changes)


def refused() -> set[tuple[str, str]]:
    """The endpoints that refused ``reasoning_content`` so far (a copy)."""
    return set(ls_mod._REPLAY_REFUSED)


def now() -> int:
    return int(time.time() * 1000)


def user(value: str) -> UserMessage:
    return UserMessage(content=value, timestamp=now())


def assistant(*blocks: dict, provider: str = "openai") -> AssistantMessage:
    return AssistantMessage(content=list(blocks), provider=provider, model="m", timestamp=now())


def thinking(value: str) -> dict:
    return {"type": "thinking", "thinking": value}


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


# ---------------------------------------------------------------------------
# The thinking rides along on the LangChain message
# ---------------------------------------------------------------------------


class TestTheThinkingRidesAlong:
    """``convert_to_langchain`` keeps the thinking of a turn where the chat model finds it."""

    def test_it_is_kept_when_the_model_entry_asks(self):
        message = assistant(thinking("let me think"), text("ok"))

        (ai,) = convert_to_langchain([message], model=gateway())

        assert ai.additional_kwargs == {"reasoning_content": "let me think"}

    def test_it_is_not_kept_when_the_model_entry_does_not_ask(self):
        message = assistant(thinking("let me think"), text("ok"))

        (ai,) = convert_to_langchain([message], model=gateway(replay=False))

        assert ai.additional_kwargs == {}

    @pytest.mark.parametrize("provider", ["anthropic", "mock", "google"])
    def test_a_provider_that_cannot_send_it_keeps_nothing(self, provider: str):
        message = assistant(thinking("let me think"), text("ok"))

        (ai,) = convert_to_langchain([message], model=gateway(provider=provider))

        assert ai.additional_kwargs == {}

    def test_the_content_keeps_its_shape(self):
        message = assistant(thinking("let me think"), text("ok"))

        (ai,) = convert_to_langchain([message], model=gateway())

        assert ai.content == [thinking("let me think"), text("ok")]


# ---------------------------------------------------------------------------
# The gate: which request carries the thinking
# ---------------------------------------------------------------------------


class TestTheGate:
    @pytest.mark.parametrize(
        ("model", "level", "expected"),
        [
            # An opt-in gateway of the OpenAI format
            (gateway(), "high", True),
            (gateway(), "minimal", True),
            (gateway(), "max", True),
            (gateway(replay=False), "high", False),
            (gateway(reasoning=False), "high", False),
            (gateway(), "off", False),
            (gateway(), None, False),
            # A gateway that serves DeepSeek, with the provider of DeepSeek
            (gateway(provider="deepseek", base_url=SILICONFLOW), "high", True),
            (gateway(provider="deepseek", base_url=SILICONFLOW, replay=False), "high", False),
            (gateway(provider="deepseek", base_url=SILICONFLOW), "off", False),
            # DeepSeek's own API: it needs no opt-in, and it never gets one without thinking
            (own_api(), "high", True),
            (gateway(provider="deepseek", base_url=DEEPSEEK_API, replay=False), "high", True),
            (own_api(), "off", False),
            (own_api(), None, False),
            (own_api(reasoning=False), "high", False),
            # A provider with no chat class that can send it
            (gateway(provider="anthropic"), "high", False),
            (gateway(provider="mock"), "high", False),
        ],
    )
    def test_the_matrix(self, model: Model, level: str | None, expected: bool):
        assert ls_mod._replays_reasoning(model, level) is expected  # type: ignore[arg-type]

    def test_a_refusal_that_was_seen_closes_the_gate_for_that_model_and_address(self):
        model = gateway()
        ls_mod._REPLAY_REFUSED.add(ls_mod._replay_key(model))

        assert ls_mod._replays_reasoning(model, "high") is False
        assert ls_mod._replays_reasoning(gateway(model_id="another"), "high") is True
        assert ls_mod._replays_reasoning(gateway(base_url="http://other.test/v1"), "high") is True

    def test_the_address_is_compared_in_one_form(self):
        ls_mod._REPLAY_REFUSED.add(ls_mod._replay_key(gateway(base_url=GATEWAY)))

        assert ls_mod._replays_reasoning(gateway(base_url=GATEWAY + "/"), "high") is False
        assert ls_mod._replays_reasoning(gateway(base_url=GATEWAY.upper()), "high") is False

    def test_deepseek_own_api_does_not_close_the_gate(self):
        """It answers 400 without the thinking, so a refusal there is a different error."""
        model = own_api()
        ls_mod._REPLAY_REFUSED.add(ls_mod._replay_key(model))

        assert ls_mod._replays_reasoning(model, "high") is True

    def test_the_chat_model_gets_the_answer_of_the_gate(self):
        on = ls_mod.resolve_chat_model(gateway(), "sk-test", "high")
        off = ls_mod.resolve_chat_model(gateway(replay=False), "sk-test", "high")
        thinking_off = ls_mod.resolve_chat_model(gateway(), "sk-test", "off")

        assert type(on) is reasoning_chat_openai()
        assert on.replay_reasoning is True
        assert off.replay_reasoning is False
        assert thinking_off.replay_reasoning is False

    def test_a_deepseek_gateway_chat_model_gets_the_answer_of_the_gate(self):
        pytest.importorskip("langchain_deepseek")

        on = ls_mod.resolve_chat_model(
            gateway(provider="deepseek", base_url=SILICONFLOW), "sk-test", "high"
        )
        off = ls_mod.resolve_chat_model(
            gateway(provider="deepseek", base_url=SILICONFLOW, replay=False), "sk-test", "high"
        )

        assert on.replay_reasoning is True
        assert off.replay_reasoning is False


# ---------------------------------------------------------------------------
# What the chat model sends
# ---------------------------------------------------------------------------


class TestWhatTheChatModelSends:
    @staticmethod
    def assistants(model: Model, level: str | None = "high") -> list[dict]:
        chat = ls_mod.resolve_chat_model(model, "sk-test", level)
        payload = chat._get_request_payload(history_for(model))
        return [m for m in payload["messages"] if m["role"] == "assistant"]

    def test_the_thinking_goes_back_on_every_assistant_message(self):
        first, second = self.assistants(gateway())

        assert first["reasoning_content"] == "plan"
        assert second["reasoning_content"] == "wrap up"

    def test_a_tool_call_message_has_a_string_content(self):
        first, _ = self.assistants(gateway())

        assert first["content"] == ""
        assert first["tool_calls"][0]["id"] == "call_1"

    def test_the_text_of_an_answer_is_plain_text_not_a_list_of_blocks(self):
        _, second = self.assistants(gateway())

        assert second["content"] == "done"

    def test_a_message_of_thinking_only_has_an_empty_content_not_an_empty_list(self):
        model = gateway()
        chat = ls_mod.resolve_chat_model(model, "sk-test", "high")
        messages = convert_to_langchain(
            [user("q"), assistant(thinking("only this")), user("go on")], model=model
        )

        _, only, _ = chat._get_request_payload(messages)["messages"]

        assert only == {"role": "assistant", "content": "", "reasoning_content": "only this"}

    def test_a_message_without_thinking_gets_an_empty_field(self):
        model = gateway()
        chat = ls_mod.resolve_chat_model(model, "sk-test", "high")
        messages = convert_to_langchain([user("q"), assistant(text("hello"))], model=model)

        _, answer = chat._get_request_payload(messages)["messages"]

        assert answer == {"role": "assistant", "content": "hello", "reasoning_content": ""}

    def test_the_other_roles_are_as_langchain_built_them(self):
        model = gateway()
        chat = ls_mod.resolve_chat_model(model, "sk-test", "high")
        wire = chat._get_request_payload(history_for(model))["messages"]

        assert [m["role"] for m in wire] == ["user", "assistant", "tool", "assistant", "user"]
        assert wire[0] == {"role": "user", "content": "q1"}
        assert wire[2] == {"role": "tool", "content": "hi", "tool_call_id": "call_1"}

    def test_the_rest_of_the_request_is_as_before(self):
        chat = ls_mod.resolve_chat_model(gateway(), "sk-test", "high")

        payload = chat._get_request_payload([HumanMessage("hi")], stop=["END"], stream=True)

        assert payload["stop"] == ["END"]
        assert payload["stream"] is True
        assert payload["reasoning_effort"] == "high"
        assert payload["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.parametrize("level", ["off", None])
    def test_nothing_is_sent_when_thinking_is_off(self, level: str | None):
        for message in self.assistants(gateway(), level):
            assert "reasoning_content" not in message

    def test_nothing_is_sent_for_a_model_that_cannot_reason(self):
        for message in self.assistants(gateway(reasoning=False)):
            assert "reasoning_content" not in message

    def test_nothing_is_sent_without_the_opt_in(self):
        """The request that ``provider = "openai"`` has always sent."""
        first, second = self.assistants(gateway(replay=False))

        assert "reasoning_content" not in first
        assert "reasoning_content" not in second
        assert first["content"] is None
        assert second["content"] == [text("done")]

    def test_the_request_has_the_shape_that_the_deepseek_provider_sends(self):
        """The same history gives the same assistant messages as ``ChatDeepSeek``."""
        pytest.importorskip("langchain_deepseek")
        via_openai = self.assistants(gateway())
        via_deepseek = self.assistants(gateway(provider="deepseek", base_url=SILICONFLOW))

        assert via_openai == via_deepseek

    def test_a_request_of_the_responses_api_is_left_alone(self):
        """It has ``input``, not ``messages``: there is no message to put the thinking into."""
        model = gateway()
        chat = reasoning_chat_openai()(
            model=model.model_id, api_key="sk-test", use_responses_api=True, replay_reasoning=True
        )

        payload = chat._get_request_payload(history_for(model))

        assert "messages" not in payload
        assert "reasoning_content" not in str(payload["input"])


# ---------------------------------------------------------------------------
# The whole chain, with the HTTP call of the real chat class faked
# ---------------------------------------------------------------------------


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


def refusal(status: int = 400) -> openai.APIStatusError:
    """The error of a gateway that checks the fields of a message."""
    if status == 422:
        return api_error(
            422,
            "[{'type': 'extra_forbidden', 'loc': ['body', 'messages', 1, 'assistant', "
            "'reasoning_content'], 'msg': 'Extra inputs are not permitted'}]",
        )
    return api_error(400, "Unknown parameter: 'messages[1].reasoning_content'.")


def carries_thinking(payload: dict) -> bool:
    return any(
        "reasoning_content" in message
        for message in payload["messages"]
        if message.get("role") == "assistant"
    )


class _FakeCompletions:
    """Stands in for ``ChatOpenAI.async_client``: records each request, streams canned replies.

    ``refuse`` looks at a request and returns the error to raise for it, or ``None``.
    """

    def __init__(
        self,
        *replies: list[dict],
        refuse: Callable[[dict], BaseException | None] | None = None,
    ) -> None:
        self.requests: list[dict] = []
        self._replies = list(replies)
        self._refuse = refuse

    async def create(self, **payload: Any) -> _Stream:
        self.requests.append(payload)
        error = self._refuse(payload) if self._refuse is not None else None
        if error is not None:
            raise error
        return _Stream(self._replies.pop(0))


def strict_gateway(status: int = 400) -> Callable[[dict], BaseException | None]:
    """A gateway that refuses ``reasoning_content``, like the OpenAI API."""
    return lambda payload: refusal(status) if carries_thinking(payload) else None


def echo_tool() -> SimpleTool:
    return SimpleTool(
        name="echo",
        description="echo",
        label="echo",
        parameters={"type": "object", "properties": {"m": {"type": "string"}}},
        execute_fn=lambda *a: None,
    )


async def run_turn(
    model: Model,
    messages: list[Any],
    *,
    tools: list[SimpleTool] | None = None,
    reasoning: str = "high",
    max_retries: int = 2,
):
    stream = await ls_mod.langchain_stream(
        model,
        LlmContext(system_prompt=None, messages=messages, tools=tools or []),
        StreamOptions(reasoning=reasoning, api_key="sk-test", max_retries=max_retries),  # type: ignore[arg-type]
    )
    events = [event async for event in stream]
    return events, await stream.message_result()


async def tool_round(model: Model, tools: list[SimpleTool], **options: Any):
    """Turn 1 (the model calls a tool) and turn 2 (it answers).

    The history of turn 2 has the thinking of turn 1. Returns the messages, turn 1 and turn 2.
    """
    messages: list[Any] = [user("say hi")]
    _, first = await run_turn(model, messages, tools=tools, **options)
    messages += [first, result()]
    _, second = await run_turn(model, messages, tools=tools, **options)
    return messages, first, second


@pytest.mark.asyncio
async def test_a_tool_round_trip_sends_the_thinking_back(monkeypatch: pytest.MonkeyPatch):
    """The stream captures the thinking, the next request carries it: the whole chain."""
    model = gateway()
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND)
    wire_up(monkeypatch, fake)

    _, first, second = await tool_round(model, [echo_tool()])

    assert first.content[0] == thinking("I should call echo")
    assert second.content == [thinking("all good"), text("hi there")]
    assert "reasoning_content" not in str(fake.requests[0]["messages"])
    wire = fake.requests[1]["messages"]
    assert [m["role"] for m in wire] == ["user", "assistant", "tool"]
    assert wire[1]["reasoning_content"] == "I should call echo"
    assert wire[1]["content"] == ""
    assert wire[1]["tool_calls"][0]["id"] == "call_1"
    assert fake.requests[1]["stream"] is True
    assert len(fake.requests) == 2


@pytest.mark.asyncio
async def test_nothing_goes_back_while_thinking_is_off(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND)
    wire_up(monkeypatch, fake)

    await tool_round(gateway(), [echo_tool()], reasoning="off")

    assert "reasoning_content" not in str(fake.requests[1]["messages"])


@pytest.mark.asyncio
async def test_nothing_goes_back_without_the_opt_in(monkeypatch: pytest.MonkeyPatch):
    """The default: what ``provider = "openai"`` has always sent."""
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND)
    wire_up(monkeypatch, fake)

    await tool_round(gateway(replay=False), [echo_tool()])

    assert "reasoning_content" not in str(fake.requests[1]["messages"])
    assert len(fake.requests) == 2


# ---- a gateway that refuses the field ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 422])
async def test_a_gateway_that_refuses_the_field_is_asked_again_without_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, status: int
):
    model = gateway()
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND, refuse=strict_gateway(status))
    wire_up(monkeypatch, fake)

    with caplog.at_level(logging.WARNING, logger="pi_agent_core.langchain_stream"):
        _, _, second = await tool_round(model, [echo_tool()])

    # The answer is the one of the second request; the user sees no error.
    assert second.stopReason == "stop"
    assert second.content == [thinking("all good"), text("hi there")]
    assert len(fake.requests) == 3
    assert carries_thinking(fake.requests[1])
    assert not carries_thinking(fake.requests[2])
    # The request that was asked again is the plain one, as it was before the opt-in existed.
    plain = fake.requests[2]["messages"]
    assert [m["role"] for m in plain] == ["user", "assistant", "tool"]
    assert plain[1]["content"] in (None, "", [])
    assert refused() == {ls_mod._replay_key(model)}
    messages = [r.getMessage() for r in caplog.records]
    assert any("refused reasoning_content" in m and "Asking again" in m for m in messages)
    assert any("replay_reasoning = false" in m for m in messages)


@pytest.mark.asyncio
async def test_after_a_refusal_the_next_request_does_not_try_again(
    monkeypatch: pytest.MonkeyPatch,
):
    model = gateway()
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND, PLAIN_ROUND, refuse=strict_gateway())
    wire_up(monkeypatch, fake)
    tools = [echo_tool()]
    messages, _, second = await tool_round(model, tools)
    seen = len(fake.requests)

    messages += [second, user("once more")]
    _, third = await run_turn(model, messages, tools=tools)

    assert third.content == [text("hello world")]
    assert len(fake.requests) == seen + 1  # one request, and it was not refused
    assert not carries_thinking(fake.requests[-1])


@pytest.mark.asyncio
async def test_the_refusal_costs_no_retry_of_the_transient_kind(monkeypatch: pytest.MonkeyPatch):
    """Asking again without the thinking is not one of ``max_retries``."""
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND, refuse=strict_gateway())
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(gateway(), [echo_tool()], max_retries=0)

    assert second.stopReason == "stop"
    assert len(fake.requests) == 3


@pytest.mark.asyncio
async def test_an_error_that_does_not_name_the_field_is_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
):
    """A request that grew too large with the thinking in it must not drop the thinking."""
    too_large = api_error(400, "This model's maximum context length is 1000 tokens.")
    fake = _FakeCompletions(TOOL_ROUND, refuse=lambda p: too_large if carries_thinking(p) else None)
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(gateway(), [echo_tool()])

    assert second.stopReason == "error"
    assert "maximum context length" in (second.errorMessage or "")
    assert len(fake.requests) == 2  # the error is the answer: no request without the thinking
    assert refused() == set()


@pytest.mark.asyncio
async def test_a_refusal_of_another_field_is_not_a_refusal_of_the_thinking(
    monkeypatch: pytest.MonkeyPatch,
):
    other = api_error(400, "Unrecognized request argument supplied: reasoning_effort")
    fake = _FakeCompletions(TOOL_ROUND, refuse=lambda p: other if carries_thinking(p) else None)
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(gateway(), [echo_tool()])

    assert second.stopReason == "error"
    assert len(fake.requests) == 2
    assert refused() == set()


@pytest.mark.asyncio
async def test_a_request_that_fails_again_without_the_thinking_is_an_error_and_is_not_kept(
    monkeypatch: pytest.MonkeyPatch,
):
    """The thinking was not the only reason: nothing is learned about it."""
    unavailable = api_error(400, "The model is not available.")

    def refuse(payload: dict) -> BaseException | None:
        if len(payload["messages"]) > 1:
            return refusal() if carries_thinking(payload) else unavailable
        return None

    fake = _FakeCompletions(TOOL_ROUND, refuse=refuse)
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(gateway(), [echo_tool()])

    assert second.stopReason == "error"
    assert "not available" in (second.errorMessage or "")
    assert len(fake.requests) == 3
    assert refused() == set()


@pytest.mark.asyncio
async def test_a_refusal_after_the_first_chunk_is_not_asked_again(monkeypatch: pytest.MonkeyPatch):
    """Chunks that were shown cannot be taken back: the error is the answer."""

    class _Breaking(_FakeCompletions):
        async def create(self, **payload: Any) -> _Stream:
            self.requests.append(payload)
            if carries_thinking(payload):
                return _Stream(ANSWER_ROUND[:2], fail_after=refusal())
            return _Stream(self._replies.pop(0))

    fake = _Breaking(TOOL_ROUND)
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(gateway(), [echo_tool()])

    assert second.stopReason == "error"
    assert len(fake.requests) == 2
    assert refused() == set()


@pytest.mark.asyncio
async def test_a_refusal_is_kept_for_the_model_that_it_was_seen_for(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND, refuse=strict_gateway())
    wire_up(monkeypatch, fake)
    await tool_round(gateway(), [echo_tool()])

    assert refused() == {(GATEWAY, "deepseek-flash")}
    assert ls_mod._replays_reasoning(gateway(model_id="deepseek-pro"), "high") is True


@pytest.mark.asyncio
async def test_deepseek_own_api_is_not_asked_again_without_the_thinking(
    monkeypatch: pytest.MonkeyPatch,
):
    """It answers 400 to a request without the thinking, so dropping it could not help."""
    pytest.importorskip("langchain_deepseek")
    model = own_api()
    fake = _FakeCompletions(TOOL_ROUND, refuse=strict_gateway())
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(model, [echo_tool()])

    assert second.stopReason == "error"
    assert len(fake.requests) == 2
    assert carries_thinking(fake.requests[1])
    assert refused() == set()


@pytest.mark.asyncio
async def test_a_deepseek_gateway_that_refuses_the_field_is_asked_again_without_it(
    monkeypatch: pytest.MonkeyPatch,
):
    pytest.importorskip("langchain_deepseek")
    model = gateway(provider="deepseek", base_url=SILICONFLOW)
    fake = _FakeCompletions(TOOL_ROUND, ANSWER_ROUND, refuse=strict_gateway())
    wire_up(monkeypatch, fake)

    _, _, second = await tool_round(model, [echo_tool()])

    assert second.stopReason == "stop"
    assert len(fake.requests) == 3
    assert not carries_thinking(fake.requests[2])
    assert refused() == {(SILICONFLOW, "deepseek-flash")}


class TestWhatCountsAsARefusal:
    @pytest.mark.parametrize("status", [400, 422])
    def test_a_400_or_422_that_names_the_field(self, status: int):
        assert ls_mod._is_replay_refusal(refusal(status)) is True

    def test_the_name_is_read_in_any_case(self):
        error = api_error(400, "Additional properties are not allowed ('Reasoning_Content')")

        assert ls_mod._is_replay_refusal(error) is True

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
    def test_another_status_is_not(self, status: int):
        assert ls_mod._is_replay_refusal(api_error(status, "reasoning_content")) is False

    @pytest.mark.parametrize(
        "message",
        [
            "maximum context length is 1000 tokens",
            "Unrecognized request argument supplied: reasoning_effort",
            "",
        ],
    )
    def test_a_400_that_does_not_name_the_field_is_not(self, message: str):
        assert ls_mod._is_replay_refusal(api_error(400, message)) is False

    def test_an_error_with_no_status_is_not(self):
        assert ls_mod._is_replay_refusal(RuntimeError("reasoning_content")) is False
