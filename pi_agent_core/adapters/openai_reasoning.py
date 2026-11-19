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
from typing import Any, Literal

from langchain_core.messages import AIMessageChunk

REASONING_CONTENT = "reasoning_content"
GOOGLE_THOUGHT_FLAG = "_google_thought"

# The names that gateways use for the thinking of a streamed delta, the first one that is
# a non-empty string wins. ``ChatDeepSeek`` reads the same two names, in the same order.
_DELTA_KEYS = (REASONING_CONTENT, "reasoning")

_OPEN_THOUGHT_TAG = "<thought>"
_CLOSE_THOUGHT_TAG = "</thought>"


def _extract_reasoning_details(details: Any) -> str:
    """Extract thinking text from OpenRouter-style ``delta.reasoning_details`` lists."""
    if not isinstance(details, list):
        return ""
    parts: list[str] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "reasoning.text":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif kind == "reasoning.summary":
            summary = item.get("summary")
            if isinstance(summary, str) and summary:
                parts.append(summary)
    return "".join(parts)


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

    extra_content = delta.get("extra_content")
    if isinstance(extra_content, dict):
        google = extra_content.get("google")
        if isinstance(google, dict) and google.get("thought") is True:
            message.additional_kwargs[GOOGLE_THOUGHT_FLAG] = True

    for key in _DELTA_KEYS:
        value = delta.get(key)
        if isinstance(value, str) and value:
            message.additional_kwargs[REASONING_CONTENT] = value
            return

    details_text = _extract_reasoning_details(delta.get("reasoning_details"))
    if details_text:
        message.additional_kwargs[REASONING_CONTENT] = details_text


def _partial_tag_suffix_len(text: str, tag: str) -> int:
    """Length of the longest non-empty prefix of *tag* at the end of *text*."""
    max_len = min(len(text), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


class ThoughtTagSplitter:
    """Per-stream splitter for Gemini OpenAI-compat ``<thought>...</thought>`` chunks.

    Google's OpenAI-compatible endpoint streams thoughts inside ``delta.content`` with a
    leading ``<thought>`` tag (and ``extra_content.google.thought: true`` on thought chunks),
    often ending with ``</thought>`` concatenated into the same chunk as the first visible
    answer token. One instance is created per stream so concurrent streams share no state.
    """

    def __init__(self, *, parse_tags: bool = True) -> None:
        self._parse_tags = parse_tags
        self._state: Literal["init", "in_thought", "after_thought", "plain"] = "init"
        self._buf: str = ""
        self._saw_google_thought: bool = False
        self._has_open_tag: bool = False

    def feed(self, text: str, *, is_google_thought: bool = False) -> tuple[str, str]:
        """Process one streamed text delta and return ``(thinking_delta, text_delta)``."""
        if self._state in ("plain", "after_thought"):
            if not is_google_thought:
                return ("", text)
            self._state = "init"
            self._buf = ""
            self._saw_google_thought = True
            self._has_open_tag = False

        if self._state == "init":
            if is_google_thought:
                self._saw_google_thought = True
            if not text:
                return ("", "")
            if not self._parse_tags and not self._saw_google_thought:
                self._state = "plain"
                return ("", text)
            buf = self._buf + text
            self._buf = ""
            stripped = buf.lstrip(" \t\r\n")
            ws_len = len(buf) - len(stripped)
            if ws_len <= 16:
                if stripped == "":
                    self._buf = buf
                    return ("", "")
                if stripped.startswith(_OPEN_THOUGHT_TAG):
                    self._state = "in_thought"
                    self._has_open_tag = True
                    return self._consume_in_thought(
                        stripped[len(_OPEN_THOUGHT_TAG) :],
                        is_google_thought=is_google_thought,
                    )
                if _OPEN_THOUGHT_TAG.startswith(stripped):
                    self._buf = buf
                    return ("", "")
            if self._saw_google_thought:
                self._state = "in_thought"
                self._has_open_tag = False
                return self._consume_in_thought(buf, is_google_thought=is_google_thought)
            self._state = "plain"
            return ("", buf)

        return self._consume_in_thought(text, is_google_thought=is_google_thought)

    def _consume_in_thought(self, text: str, *, is_google_thought: bool) -> tuple[str, str]:
        if not self._has_open_tag:
            if is_google_thought:
                return (text, "")
            self._state = "after_thought"
            return ("", text)

        buf = self._buf + text
        self._buf = ""
        end_idx = buf.find(_CLOSE_THOUGHT_TAG)
        if end_idx != -1:
            thinking_part = buf[:end_idx]
            text_part = buf[end_idx + len(_CLOSE_THOUGHT_TAG) :]
            self._state = "after_thought"
            self._has_open_tag = False
            return (thinking_part, text_part)

        keep = _partial_tag_suffix_len(buf, _CLOSE_THOUGHT_TAG)
        if keep:
            self._buf = buf[-keep:]
            return (buf[:-keep], "")
        return (buf, "")

    def flush(self) -> tuple[str, str]:
        """Flush any buffered partial tag at stream end or before tool calls."""
        if not self._buf:
            return ("", "")
        leftover = self._buf
        self._buf = ""
        if self._state == "in_thought" or (self._state == "init" and self._saw_google_thought):
            return (leftover, "")
        self._state = "plain"
        return ("", leftover)


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
