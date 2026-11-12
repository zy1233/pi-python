"""Keep the thinking that an OpenAI-compatible gateway streams as ``reasoning_content``.

Many gateways (LiteLLM, vLLM, SiliconFlow, and others) put the thinking of a model in
``delta.reasoning_content``; some (OpenRouter, newer vLLM, Ollama) call it ``delta.reasoning``.
Neither is part of the OpenAI Chat Completions format, and ``langchain_openai.ChatOpenAI`` does
not read them: its documentation says so, and it drops both. The adapter then never sees any
thinking, and the client shows none.

``ChatDeepSeek`` keeps them, so a user could set ``provider = "deepseek"``. A frozen build
(``zycode-agent``) has no ``langchain_deepseek``, and ``provider = "openai"`` with a ``base_url``
is the one way to reach a gateway there. This module makes that way keep the thinking too.

The change is the same one that ``ChatDeepSeek`` makes: after LangChain has turned a streamed
chunk into a message chunk, the thinking of the chunk is put into
``additional_kwargs["reasoning_content"]``, where ``langchain_stream`` reads it. A chunk with
no such field is not touched, so api.openai.com is not affected.

Only the streamed answer is changed (the adapter always streams). The request is not changed:
nothing is sent back, and ``convert_to_langchain`` already leaves thinking blocks out of a
request for this provider (``ChatOpenAI`` drops the ``thinking`` content blocks). A provider
that wants the thinking back (DeepSeek's own API) is ``provider = "deepseek"``
(``deepseek_replay``).
"""

from __future__ import annotations

import functools
from typing import Any

from langchain_core.messages import AIMessageChunk

REASONING_CONTENT = "reasoning_content"

# The names that gateways use for the thinking of a streamed delta, the first one that is
# a non-empty string wins. ``ChatDeepSeek`` reads the same two names, in the same order.
_DELTA_KEYS = (REASONING_CONTENT, "reasoning")


def keep_streamed_reasoning(chunk: Any, generation_chunk: Any) -> None:
    """Copy the thinking of one streamed *chunk* into *generation_chunk*, in place.

    *chunk* is the raw chunk of the API (a dict) and *generation_chunk* is what LangChain made
    of it (or ``None``, when it made nothing). Anything that does not look as expected is
    left alone: a missing thinking is the old, known behaviour.
    """
    if generation_chunk is None or not isinstance(chunk, dict):
        return
    message = getattr(generation_chunk, "message", None)
    if not isinstance(message, AIMessageChunk):
        return
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return
    for key in _DELTA_KEYS:
        value = delta.get(key)
        if isinstance(value, str) and value:
            message.additional_kwargs[REASONING_CONTENT] = value
            return


@functools.cache
def reasoning_chat_openai() -> type:
    """``ChatOpenAI`` that keeps the thinking that a gateway streams.

    Built on first use: ``langchain-openai`` is an optional dependency.
    """
    from langchain_openai import ChatOpenAI

    class ReasoningChatOpenAI(ChatOpenAI):  # type: ignore[misc]
        # ``*args`` and ``**kwargs``: this is a private LangChain method, and a later release
        # may add a parameter. It is passed on as it is.
        def _convert_chunk_to_generation_chunk(self, chunk: Any, *args: Any, **kwargs: Any) -> Any:
            generation_chunk = super()._convert_chunk_to_generation_chunk(chunk, *args, **kwargs)
            keep_streamed_reasoning(chunk, generation_chunk)
            return generation_chunk

    return ReasoningChatOpenAI
