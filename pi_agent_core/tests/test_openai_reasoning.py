"""``provider = "openai"`` keeps the thinking that a gateway streams as ``reasoning_content``.

Found in the VS Code extension: its bundled agent has only the OpenAI-format provider (no
``langchain_deepseek``), so a DeepSeek model behind a gateway has to use ``provider = "openai"``,
and plain ``ChatOpenAI`` dropped ``delta.reasoning_content``. No thinking reached the client.
The sessions of the same model with ``provider = "deepseek"`` had thinking blocks; the sessions
with ``provider = "openai"`` had none.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

pytest.importorskip("langchain_openai")

from langchain_openai import ChatOpenAI

from pi_agent_core.adapters.langchain_convert import convert_to_langchain
from pi_agent_core.adapters.openai_reasoning import (
    GOOGLE_THOUGHT_FLAG,
    ThoughtTagSplitter,
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


# ---------------------------------------------------------------------------
# Gemini OpenAI-compat & OpenRouter reasoning extraction
# ---------------------------------------------------------------------------


class TestGoogleThoughtAndReasoningDetails:
    def test_google_thought_flag_is_kept(self):
        made = generation_chunk("<thought>step")

        keep_streamed_reasoning(
            raw({"content": "<thought>step", "extra_content": {"google": {"thought": True}}}),
            made,
        )

        assert made.message.additional_kwargs == {GOOGLE_THOUGHT_FLAG: True}

    @pytest.mark.parametrize(
        "extra_content",
        [
            {"google": {"thought": False}},
            {"google": {"thought_signature": "sig"}},
            {"google": {}},
            {},
            None,
        ],
    )
    def test_google_thought_flag_ignored_when_not_true(self, extra_content: Any):
        made = generation_chunk("hi")

        keep_streamed_reasoning(raw({"content": "hi", "extra_content": extra_content}), made)

        assert made.message.additional_kwargs == {}

    def test_reasoning_details_text_and_summary_extracted(self):
        made = generation_chunk()

        keep_streamed_reasoning(
            raw(
                {
                    "reasoning_details": [
                        {"type": "reasoning.text", "text": "step 1. "},
                        {"type": "reasoning.summary", "summary": "summary"},
                        {"type": "reasoning.encrypted", "data": "ignored"},
                    ]
                }
            ),
            made,
        )

        assert made.message.additional_kwargs == {"reasoning_content": "step 1. summary"}

    def test_reasoning_content_still_wins_over_reasoning_details(self):
        made = generation_chunk()

        keep_streamed_reasoning(
            raw(
                {
                    "reasoning_content": "primary",
                    "reasoning_details": [{"type": "reasoning.text", "text": "secondary"}],
                }
            ),
            made,
        )

        assert made.message.additional_kwargs == {"reasoning_content": "primary"}


class TestThoughtTagSplitter:
    def _run_chunks(
        self, chunks: list[tuple[str, bool]], *, parse_tags: bool = True
    ) -> tuple[str, str]:
        splitter = ThoughtTagSplitter(parse_tags=parse_tags)
        thoughts: list[str] = []
        texts: list[str] = []
        for text, flag in chunks:
            t, v = splitter.feed(text, is_google_thought=flag)
            if t:
                thoughts.append(t)
            if v:
                texts.append(v)
        t, v = splitter.flush()
        if t:
            thoughts.append(t)
        if v:
            texts.append(v)
        return "".join(thoughts), "".join(texts)

    def test_real_gemini_sequence(self):
        thought, text = self._run_chunks(
            [
                ("<thought>step 1\n", True),
                ("</thought>7", False),
            ]
        )
        assert thought == "step 1\n"
        assert text == "7"

    def test_single_chunk(self):
        thought, text = self._run_chunks([("<thought>a</thought>b", True)])
        assert thought == "a"
        assert text == "b"

    def test_split_opening_and_closing_tags(self):
        thought, text = self._run_chunks(
            [
                ("<tho", True),
                ("ught>hi", True),
                ("</thou", True),
                ("ght>ans", False),
            ]
        )
        assert thought == "hi"
        assert text == "ans"

    def test_flag_only_without_tags(self):
        thought, text = self._run_chunks(
            [
                ("thinking", True),
                ("answer", False),
            ]
        )
        assert thought == "thinking"
        assert text == "answer"

    def test_normal_text_starting_with_angle_bracket_not_swallowed(self):
        assert self._run_chunks([("<div>hi</div>", False)]) == ("", "<div>hi</div>")
        assert self._run_chunks([("<", False)]) == ("", "<")

    def test_mid_answer_thought_tag_in_plain_text_stays_plain(self):
        thought, text = self._run_chunks(
            [
                ("see ", False),
                ("<thought> tag", False),
            ]
        )
        assert thought == ""
        assert text == "see <thought> tag"

    def test_parse_tags_false_preserves_literal_thought_tag_unless_google_flagged(self):
        assert self._run_chunks([("<thought>x</thought>done", False)], parse_tags=False) == (
            "",
            "<thought>x</thought>done",
        )
        assert self._run_chunks([("<thought>x</thought>done", True)], parse_tags=False) == (
            "x",
            "done",
        )
        assert self._run_chunks(
            [("thinking", True), ("answer", False)],
            parse_tags=False,
        ) == ("thinking", "answer")

    def test_leading_whitespace_before_thought_tag_and_whitespace_only(self):
        assert self._run_chunks([("\n<thought>a</thought>x", False)]) == ("a", "x")
        assert self._run_chunks([("\n", False), ("<thought>a</thought>x", False)]) == ("a", "x")
        assert self._run_chunks([("   \n", False)]) == ("", "   \n")

    def test_unclosed_and_empty_thought_tags(self):
        assert self._run_chunks([("<thought>never closes", False)]) == ("never closes", "")
        assert self._run_chunks([("<thought></thought>answer", False)]) == ("", "answer")


def gemini_model(*, thinking_format: str | None = "gemini") -> Model:
    return Model(
        provider="openai",
        model_id="google/models/gemini-4-hydrogen",
        base_url=GATEWAY,
        reasoning=True,
        thinking_format=thinking_format,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_gemini_wire_json_sends_nested_extra_body_without_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        sse = (
            'data: {"id":"1","object":"chat.completion.chunk","created":0,'
            '"model":"google/models/gemini-4-hydrogen",'
            '"choices":[{"index":0,"delta":{"role":"assistant","content":"ok"},'
            '"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse.encode("utf-8"),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        model = gemini_model(thinking_format="gemini")
        real_resolve = ls_mod.resolve_chat_model

        def fake_resolve(m: Model, api_key: str | None = None, reasoning: Any = None, **kw: Any):
            chat = real_resolve(m, api_key, reasoning, **kw)
            chat.http_async_client = http_client
            # Rebuild async_client to use our MockTransport.
            chat.async_client = None
            import openai

            chat.root_async_client = openai.AsyncOpenAI(
                api_key=api_key or "sk-test",
                base_url=m.base_url,
                http_client=http_client,
            )
            chat.async_client = chat.root_async_client.chat.completions
            return chat

        monkeypatch.setattr(ls_mod, "resolve_chat_model", fake_resolve)

        stream = await ls_mod.langchain_stream(
            model,
            LlmContext(system_prompt=None, messages=[user("hi")]),
            StreamOptions(reasoning="max", api_key="sk-test"),
        )
        _ = [e async for e in stream]
        await stream.message_result()

        assert len(captured) == 1
        wire = captured[0]
        assert "reasoning_effort" not in wire
        assert wire["extra_body"] == {
            "google": {
                "thinking_config": {
                    "include_thoughts": True,
                    "thinking_level": "high",
                }
            }
        }
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_gemini_sse_stream_splits_thought_and_text(monkeypatch: pytest.MonkeyPatch):
    model = gemini_model(thinking_format="gemini")
    gemini_round = [
        _chunk(
            {
                "role": "assistant",
                "content": "<thought>step 1\n",
                "extra_content": {"google": {"thought": True}},
            }
        ),
        _chunk({"content": "</thought>7"}),
        _chunk(
            {"extra_content": {"google": {"thought_signature": "sig-abc"}}},
            finish="stop",
        ),
    ]
    wire_up(monkeypatch, model, gemini_round)

    events, final = await run_turn(model, [user("solve")])

    kinds = [e.type for e in events]
    assert "thinking_start" in kinds
    assert "thinking_end" in kinds
    assert "text_start" in kinds
    assert kinds.index("thinking_end") < kinds.index("text_start")
    assert [e.delta for e in events if e.type == "thinking_delta"] == ["step 1\n"]
    assert [e.delta for e in events if e.type == "text_delta"] == ["7"]
    assert final.content == [
        {"type": "thinking", "thinking": "step 1\n"},
        {"type": "text", "text": "7"},
    ]


@pytest.mark.asyncio
async def test_gemini_thinking_followed_by_tool_call(monkeypatch: pytest.MonkeyPatch):
    model = gemini_model(thinking_format="gemini")
    gemini_tool_round = [
        _chunk(
            {
                "role": "assistant",
                "content": "<thought>let me call echo</thought>",
                "extra_content": {"google": {"thought": True}},
            }
        ),
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"m": "hi"}'},
                        "extra_content": {"google": {"thought_signature": "sig-tc"}},
                    }
                ]
            },
            finish="tool_calls",
        ),
    ]
    wire_up(monkeypatch, model, gemini_tool_round)

    events, final = await run_turn(model, [user("say hi")], tools=[echo_tool()])

    assert [e.delta for e in events if e.type == "thinking_delta"] == ["let me call echo"]
    assert not [e for e in events if e.type == "text_delta"]
    assert final.content[0] == {"type": "thinking", "thinking": "let me call echo"}
    assert final.content[1]["type"] == "toolCall"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thinking_format", "reasoning_cap", "reasoning_level"),
    [
        (None, True, "high"),
        ("openai", True, "high"),
        ("gemini", False, "high"),
        ("gemini", True, "off"),
    ],
)
async def test_literal_thought_tag_stays_text_when_tag_splitting_is_gated(
    monkeypatch: pytest.MonkeyPatch,
    thinking_format: str | None,
    reasoning_cap: bool,
    reasoning_level: str,
):
    """Non-Gemini or disabled-thinking OpenAI streams must not swallow literal ``<thought>``."""
    model = Model(
        provider="openai",
        model_id="gpt-4o",
        base_url=GATEWAY,
        reasoning=reasoning_cap,
        thinking_format=thinking_format,  # type: ignore[arg-type]
    )
    plain_round = [
        _chunk({"role": "assistant", "content": "<thought>"}),
        _chunk({"content": "x</thought>done"}, finish="stop"),
    ]
    fake = _FakeCompletions(plain_round)
    chat = ls_mod.resolve_chat_model(model, "sk-test", reasoning_level)
    chat.async_client = fake
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: chat)

    stream = await ls_mod.langchain_stream(
        model,
        LlmContext(system_prompt=None, messages=[user("hi")]),
        StreamOptions(reasoning=reasoning_level, api_key="sk-test"),  # type: ignore[arg-type]
    )
    events = [e async for e in stream]
    final = await stream.message_result()

    assert not [e for e in events if e.type.startswith("thinking")]
    assert final.content == [{"type": "text", "text": "<thought>x</thought>done"}]


@pytest.mark.asyncio
async def test_google_thought_flag_splits_without_tags_or_gemini_format(
    monkeypatch: pytest.MonkeyPatch,
):
    """Stream chunks flagged ``extra_content.google.thought=true`` split even without tags."""
    model = gateway_model()
    flagged_round = [
        _chunk(
            {
                "role": "assistant",
                "content": "reason step",
                "extra_content": {"google": {"thought": True}},
            }
        ),
        _chunk({"content": "final answer"}, finish="stop"),
    ]
    wire_up(monkeypatch, model, flagged_round)

    events, final = await run_turn(model, [user("solve")])

    assert [e.delta for e in events if e.type == "thinking_delta"] == ["reason step"]
    assert [e.delta for e in events if e.type == "text_delta"] == ["final answer"]
    assert final.content == [
        {"type": "thinking", "thinking": "reason step"},
        {"type": "text", "text": "final answer"},
    ]


@pytest.mark.asyncio
async def test_partial_close_tag_flushes_before_tool_call_events(
    monkeypatch: pytest.MonkeyPatch,
):
    model = gemini_model(thinking_format="gemini")
    partial_tag_tool_round = [
        _chunk(
            {
                "role": "assistant",
                "content": "<thought>check</tho",
                "extra_content": {"google": {"thought": True}},
            }
        ),
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
            },
            finish="tool_calls",
        ),
    ]
    wire_up(monkeypatch, model, partial_tag_tool_round)

    events, final = await run_turn(model, [user("say hi")], tools=[echo_tool()])

    kinds = [e.type for e in events]
    thinking_deltas = [e.delta for e in events if e.type == "thinking_delta"]
    last_thinking_idx = max(i for i, k in enumerate(kinds) if k == "thinking_delta")
    assert last_thinking_idx < kinds.index("toolcall_start")
    assert thinking_deltas == ["check", "</tho"]
    assert final.content[0] == {"type": "thinking", "thinking": "check</tho"}
    assert final.content[1]["type"] == "toolCall"


@pytest.mark.asyncio
async def test_unclosed_and_empty_thought_streams(monkeypatch: pytest.MonkeyPatch):
    model = gemini_model(thinking_format="gemini")

    wire_up(
        monkeypatch,
        model,
        [_chunk({"role": "assistant", "content": "<thought>cut"}, finish="stop")],
    )
    _, unclosed_final = await run_turn(model, [user("cut")])
    assert unclosed_final.content == [{"type": "thinking", "thinking": "cut"}]

    wire_up(
        monkeypatch,
        model,
        [_chunk({"role": "assistant", "content": "<thought></thought>done"}, finish="stop")],
    )
    empty_events, empty_final = await run_turn(model, [user("done")])
    assert not [e for e in empty_events if e.type.startswith("thinking")]
    assert empty_final.content == [{"type": "text", "text": "done"}]
