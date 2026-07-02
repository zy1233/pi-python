"""Regression tests for the LangChain stream adapter (audit P0 fixes B1/B5).

Note: ``pi_agent_core.adapters`` re-exports the ``langchain_stream`` *function*,
shadowing the submodule of the same name, so the module is loaded via importlib.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk

from pi_agent_core.types import LlmContext, Model, StreamOptions

ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")


def test_extract_text_delta_shapes():
    assert ls_mod._extract_text_delta("abc") == "abc"
    assert ls_mod._extract_text_delta("") == ""
    assert ls_mod._extract_text_delta(None) == ""
    assert ls_mod._extract_text_delta([{"type": "text", "text": "a"}]) == "a"
    assert (
        ls_mod._extract_text_delta(
            [{"type": "thinking", "thinking": "t"}, {"type": "text", "text": "b"}]
        )
        == "b"
    )
    assert ls_mod._extract_text_delta([{"type": "thinking", "thinking": "t"}]) == ""


class _FakeListContentModel:
    """Mimics ChatAnthropic streaming when tools/thinking are enabled: list content."""

    def bind_tools(self, tools: Any) -> _FakeListContentModel:
        return self

    async def astream(self, messages: Any):
        yield AIMessageChunk(content=[{"type": "thinking", "thinking": "hmm ", "index": 0}])
        yield AIMessageChunk(content=[{"type": "text", "text": "Hello ", "index": 1}])
        yield AIMessageChunk(content=[{"type": "text", "text": "world", "index": 1}])


@pytest.mark.asyncio
async def test_list_content_chunks_produce_text(monkeypatch):
    """B1: list-form text chunks (Anthropic w/ tools) must not be dropped."""
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: _FakeListContentModel())

    stream = await ls_mod.langchain_stream(
        Model(provider="anthropic", model_id="claude-x"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(),
    )
    deltas: list[str] = []
    async for event in stream:
        if event.type == "text_delta":
            deltas.append(event.delta)
    final = await stream.message_result()

    assert deltas == ["Hello ", "world"]
    assert [b for b in final.content if b.get("type") == "text"] == [
        {"type": "text", "text": "Hello world"}
    ]
    # thinking must stay ordered before text
    assert final.content[0] == {"type": "thinking", "thinking": "hmm "}


def test_resolve_fallback_provider_no_duplicate_model(monkeypatch):
    """B5: init_chat_model fallback must not pass 'model' twice and must forward kwargs."""
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model: str, *, model_provider: str | None = None, **kwargs: Any):
        captured["model"] = model
        captured["model_provider"] = model_provider
        captured["kwargs"] = kwargs
        return object()

    fake_pkg = types.ModuleType("langchain")
    fake_mod = types.ModuleType("langchain.chat_models")
    fake_mod.init_chat_model = fake_init_chat_model
    fake_pkg.chat_models = fake_mod
    monkeypatch.setitem(sys.modules, "langchain", fake_pkg)
    monkeypatch.setitem(sys.modules, "langchain.chat_models", fake_mod)

    ls_mod.resolve_chat_model(
        Model(provider="google_genai", model_id="gemini-2.5-pro"), api_key="k"
    )

    assert captured["model"] == "gemini-2.5-pro"
    assert captured["model_provider"] == "google_genai"
    assert "model" not in captured["kwargs"]
    assert captured["kwargs"]["api_key"] == "k"


def test_resolve_fallback_missing_langchain(monkeypatch):
    """B5: missing 'langchain' package must raise a clear ImportError, not 'Unsupported'."""
    monkeypatch.setitem(sys.modules, "langchain", None)
    monkeypatch.setitem(sys.modules, "langchain.chat_models", None)

    with pytest.raises(ImportError, match="pip install langchain"):
        ls_mod.resolve_chat_model(Model(provider="google_genai", model_id="gemini-2.5-pro"))


class _FakeThinkingModel:
    """Streams thinking (with signature) then text, Anthropic-style."""

    async def astream(self, messages: Any):
        yield AIMessageChunk(content=[{"type": "thinking", "thinking": "step1 ", "index": 0}])
        yield AIMessageChunk(
            content=[{"type": "thinking", "thinking": "step2", "signature": "sig123", "index": 0}]
        )
        yield AIMessageChunk(content=[{"type": "text", "text": "answer", "index": 1}])


@pytest.mark.asyncio
async def test_thinking_delta_events_and_signature(monkeypatch):
    """D6: thinking streams as thinking_delta events; B7: signature is preserved."""
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: _FakeThinkingModel())

    stream = await ls_mod.langchain_stream(
        Model(provider="anthropic", model_id="claude-x", reasoning=True),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(),
    )
    thinking_deltas: list[str] = []
    async for event in stream:
        if event.type == "thinking_delta":
            thinking_deltas.append(event.delta)
    final = await stream.message_result()

    assert thinking_deltas == ["step1 ", "step2"]
    assert final.content[0] == {
        "type": "thinking",
        "thinking": "step1 step2",
        "signature": "sig123",
    }
    assert final.content[1] == {"type": "text", "text": "answer"}


class _FakeSplitUsageModel:
    """Reports usage split across chunks (input on first, output+cache on last)."""

    async def astream(self, messages: Any):
        yield AIMessageChunk(
            content="Hi",
            usage_metadata={
                "input_tokens": 200,
                "output_tokens": 0,
                "total_tokens": 200,
                "input_token_details": {"cache_read": 30, "cache_creation": 15},
            },
        )
        yield AIMessageChunk(
            content=" there",
            usage_metadata={"input_tokens": 0, "output_tokens": 80, "total_tokens": 80},
        )


@pytest.mark.asyncio
async def test_usage_aggregated_across_chunks(monkeypatch):
    """B2: usage must be summed across chunks, not read from the last chunk only."""
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: _FakeSplitUsageModel())

    stream = await ls_mod.langchain_stream(
        Model(provider="anthropic", model_id="claude-x"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(),
    )
    final = await stream.message_result()

    assert final.usage.input == 200
    assert final.usage.output == 80
    assert final.usage.totalTokens == 280
    assert final.usage.cacheRead == 30
    assert final.usage.cacheWrite == 15


class _AbortableSignal:
    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()

    async def wait_aborted(self) -> None:
        await self._event.wait()


class _HangingModel:
    """Never yields a chunk — simulates waiting on the first token."""

    def __init__(self) -> None:
        self.cancelled = False

    async def astream(self, messages: Any):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield AIMessageChunk(content="never")


@pytest.mark.asyncio
async def test_abort_interrupts_before_first_chunk(monkeypatch):
    """B4: abort must interrupt the stream while waiting for the first token."""
    fake = _HangingModel()
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: fake)
    signal = _AbortableSignal()

    stream = await ls_mod.langchain_stream(
        Model(provider="anthropic", model_id="claude-x"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(signal=signal),
    )

    async def abort_soon():
        await asyncio.sleep(0.05)
        signal.abort()

    abort_task = asyncio.ensure_future(abort_soon())

    t0 = asyncio.get_running_loop().time()
    final = await asyncio.wait_for(stream.message_result(), timeout=2)
    elapsed = asyncio.get_running_loop().time() - t0
    await abort_task

    assert final.stopReason == "aborted"
    assert elapsed < 1.5
    assert fake.cancelled, "in-flight request task must be cancelled"


@pytest.mark.asyncio
async def test_abort_before_stream_starts(monkeypatch):
    """B4: an already-aborted signal must not issue the LLM request at all."""
    called = {"n": 0}

    class _NeverCalled:
        async def astream(self, messages: Any):
            called["n"] += 1
            yield AIMessageChunk(content="nope")

    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: _NeverCalled())
    signal = _AbortableSignal()
    signal.abort()

    stream = await ls_mod.langchain_stream(
        Model(provider="anthropic", model_id="claude-x"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(signal=signal),
    )
    final = await stream.message_result()

    assert final.stopReason == "aborted"
    assert called["n"] == 0


class _FakeAPIError(Exception):
    """Duck-typed SDK error: status_code + optional response.headers."""

    def __init__(self, status_code: int, retry_after: float | None = None) -> None:
        super().__init__(f"API error {status_code}")
        self.status_code = status_code
        if retry_after is not None:
            self.response = types.SimpleNamespace(headers={"retry-after": str(retry_after)})


class _FlakyModel:
    """Fails the first `fail_times` astream calls before any chunk is produced."""

    def __init__(self, fail_times: int, status_code: int = 429) -> None:
        self.fail_times = fail_times
        self.status_code = status_code
        self.calls = 0

    async def astream(self, messages: Any):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _FakeAPIError(self.status_code)
        yield AIMessageChunk(content="recovered")


def _retry_options(max_retries: int) -> StreamOptions:
    return StreamOptions(max_retries=max_retries, retry_base_delay=0.01, retry_max_delay=0.05)


@pytest.mark.asyncio
async def test_retry_before_first_token_recovers(monkeypatch):
    """#1: transient pre-first-token failures are retried with backoff."""
    fake = _FlakyModel(fail_times=2, status_code=429)
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: fake)

    stream = await ls_mod.langchain_stream(
        Model(provider="openai", model_id="gpt-x"),
        LlmContext(system_prompt=None, messages=[]),
        _retry_options(max_retries=3),
    )
    final = await stream.message_result()

    assert final.stopReason == "stop"
    assert final.content[0] == {"type": "text", "text": "recovered"}
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_no_retry_on_non_retryable_error(monkeypatch):
    """#1: 4xx client errors (other than 408/429) fail immediately."""
    fake = _FlakyModel(fail_times=99, status_code=400)
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: fake)

    stream = await ls_mod.langchain_stream(
        Model(provider="openai", model_id="gpt-x"),
        LlmContext(system_prompt=None, messages=[]),
        _retry_options(max_retries=3),
    )
    final = await stream.message_result()

    assert final.stopReason == "error"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retry_exhausted_surfaces_error(monkeypatch):
    fake = _FlakyModel(fail_times=99, status_code=503)
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: fake)

    stream = await ls_mod.langchain_stream(
        Model(provider="openai", model_id="gpt-x"),
        LlmContext(system_prompt=None, messages=[]),
        _retry_options(max_retries=1),
    )
    final = await stream.message_result()

    assert final.stopReason == "error"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_no_retry_after_first_chunk(monkeypatch):
    """#1: once deltas were emitted, mid-stream failures must not restart the request."""

    class _MidStreamFail:
        def __init__(self) -> None:
            self.calls = 0

        async def astream(self, messages: Any):
            self.calls += 1
            yield AIMessageChunk(content="partial")
            raise _FakeAPIError(429)

    fake = _MidStreamFail()
    monkeypatch.setattr(ls_mod, "resolve_chat_model", lambda *a, **k: fake)

    stream = await ls_mod.langchain_stream(
        Model(provider="openai", model_id="gpt-x"),
        LlmContext(system_prompt=None, messages=[]),
        _retry_options(max_retries=3),
    )
    final = await stream.message_result()

    assert final.stopReason == "error"
    assert fake.calls == 1


def test_retry_delay_respects_retry_after():
    err = _FakeAPIError(429, retry_after=2.5)
    assert ls_mod._retry_delay(err, 0, 1.0, 30.0) == 2.5
    capped = _FakeAPIError(429, retry_after=120)
    assert ls_mod._retry_delay(capped, 0, 1.0, 30.0) == 30.0


def test_retry_delay_backoff_is_capped():
    err = _FakeAPIError(503)
    assert ls_mod._retry_delay(err, 10, 1.0, 30.0) <= 30.0
