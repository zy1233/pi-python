"""Send DeepSeek's ``reasoning_content`` back on the assistant messages (audit P6-03).

In thinking mode, DeepSeek's own API answers 400 to a request that carries tools unless the
earlier assistant messages bring their ``reasoning_content`` with them ("The `reasoning_content`
in the thinking mode must be passed back to the API"). The adapter already records it as a
``thinking`` block, and ``convert_to_langchain`` keeps it in ``AIMessage.additional_kwargs``;
``ChatDeepSeek`` takes the field in but leaves it out when it builds the request. This module
puts it back.

The shape follows pi's own OpenAI-compatible provider for DeepSeek: the thinking goes back as
``reasoning_content`` (blocks joined by a newline), and an assistant message that has none gets
an empty string, since the API wants the field on all of them. A tool-call message also gets a
string ``content`` rather than ``null``, as DeepSeek's own samples send it.

Only the request to DeepSeek's own API while thinking is on is changed (``resolve_chat_model``
decides). A gateway that serves DeepSeek models has its own idea of both and may refuse the field.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Sequence
from typing import Any

from langchain_core.messages import BaseMessage

REASONING_CONTENT = "reasoning_content"


def reasoning_text(blocks: Iterable[Any]) -> str:
    """The thinking of one assistant message, as DeepSeek takes it back.

    The blocks that say something, joined by a newline: pi joins them the same way.
    """
    return "\n".join(
        block["thinking"]
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "thinking"
        and str(block.get("thinking") or "").strip()
    )


def apply_reasoning_replay(messages: Sequence[BaseMessage], wire: list[dict[str, Any]]) -> bool:
    """Put each assistant message's thinking into the request's messages, in place.

    *messages* are the LangChain messages the request was built from and *wire* is what
    LangChain made of them, one entry each. If they do not line up (a LangChain release that
    drops or merges messages) nothing is touched and the answer is False: a request without the
    field is the old, known behaviour, a misplaced one would be worse.
    """
    if len(messages) != len(wire):
        return False
    for message, entry in zip(messages, wire, strict=True):
        if entry.get("role") != "assistant":
            continue
        kept = message.additional_kwargs.get(REASONING_CONTENT)
        entry[REASONING_CONTENT] = kept if isinstance(kept, str) else ""
        if entry.get("tool_calls") and entry.get("content") is None:
            entry["content"] = ""
    return True


@functools.cache
def replaying_chat_deepseek() -> type:
    """``ChatDeepSeek`` that sends ``reasoning_content`` back while ``replay_reasoning`` is on.

    Built on first use: ``langchain-deepseek`` is an optional dependency.
    """
    from langchain_deepseek import ChatDeepSeek

    class ReplayingChatDeepSeek(ChatDeepSeek):  # type: ignore[misc]
        replay_reasoning: bool = False

        def _get_request_payload(
            self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any
        ) -> dict:
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            if self.replay_reasoning and isinstance(payload.get("messages"), list):
                sent = self._convert_input(input_).to_messages()
                apply_reasoning_replay(sent, payload["messages"])
            return payload

    return ReplayingChatDeepSeek
