"""Fakes for tests that talk to a gateway of the OpenAI format, with no network.

The ``async_client`` of a LangChain ``ChatOpenAI`` is the object whose ``create`` method makes
the HTTP request. A test puts a fake with a ``create`` method in its place. The rest of the
adapter then runs as it does for a real gateway: the chat class, the request payload, the
parser of the stream, the retries. Shared by ``test_openai_replay.py`` and
``test_probe_reasoning_replay.py``.
"""

from __future__ import annotations

import importlib
from typing import Any

import httpx
import openai
import pytest

GATEWAY_URL = "http://llm-gw.example.test/v1"


class Stream:
    """What ``create`` returns for a streamed completion (an async context manager and iterator).

    With ``fail_after`` the stream raises that error after the last chunk.
    """

    def __init__(self, chunks: list[dict], fail_after: BaseException | None = None) -> None:
        self._chunks = chunks
        self._fail_after = fail_after

    async def __aenter__(self) -> Stream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk
        if self._fail_after is not None:
            raise self._fail_after


def chunk(delta: dict, finish: str | None = None) -> dict:
    """One chunk of a streamed chat completion."""
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def api_error(status: int, message: str) -> openai.APIStatusError:
    """What the OpenAI SDK raises for an HTTP error of a server."""
    request = httpx.Request("POST", f"{GATEWAY_URL}/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    kind = {400: openai.BadRequestError, 422: openai.UnprocessableEntityError}.get(
        status, openai.APIStatusError
    )
    return kind(
        f"Error code: {status} - {message}",
        response=response,
        body={"error": {"message": message}},
    )


def wire_up(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    """The adapter's own entry point, with the HTTP call of each chat model faked.

    *client* takes the place of ``async_client`` of each chat model that the adapter builds. The
    chat model is built for each request, as in a real run, so the gate is asked each time.
    """
    module = importlib.import_module("pi_agent_core.adapters.langchain_stream")
    real = module.resolve_chat_model

    def resolve(*args: Any, **kwargs: Any) -> Any:
        chat = real(*args, **kwargs)
        chat.async_client = client
        return chat

    monkeypatch.setattr(module, "resolve_chat_model", resolve)
