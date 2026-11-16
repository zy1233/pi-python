"""The context use that the agent tells a client: the ACP ``usage_update``.

A client can show how full the context window of the model is (the VS Code extension has a
Context row for it). ACP has one session update for this, ``usage_update``, with two numbers:
``used`` (the tokens in the context now) and ``size`` (the tokens that the window holds). This
module has the pure part: how the two numbers are found. ``agent.PiAcpAgent`` has the part that
sends them. The record is ``docs/specs/2026-11-13-agent-context-usage.md``.

Two rules decide what is never sent:

* **No window, no update.** ``size`` is the ``context_window`` that the user wrote for the model
  (``config.ModelChoice``). The core has a default of 128,000 tokens. That default is a
  placeholder, not a fact about the model, so it is never sent.
* **No provider number, no update.** ``used`` starts from the ``usage`` that the provider gave
  with an assistant message. A gateway can give none, and a failed or cancelled turn has none
  that can be trusted. Then the agent says nothing. A count that is only an estimate can be far
  from the truth (the estimate is 4 characters for 1 token, which is too low for Chinese,
  Japanese and Korean text).

One case has no provider number and still gets an update: a compaction. The count falls, and the
old number would stay on the screen until the next turn ends. After a compaction the number is an
estimate of the messages that remain (``estimated_context_tokens``). The next turn replaces it
with a number of the provider.

``cost`` is never sent: the agent has no price table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from acp.schema import UsageUpdate

from pi_agent_core.messages import AssistantMessage
from pi_agent_harness.compaction import (
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
)
from pi_agent_harness.session.session import build_session_context
from pi_agent_harness.types import CompactionEntry, SessionTreeEntry

# A turn that ended with one of these has no usage that can be trusted (the harness ignores
# such a message when it counts the context for compaction, too).
_NO_NUMBER = ("error", "aborted")


def usage_update(used: int, size: int) -> UsageUpdate:
    """The ACP ``usage_update`` for ``used`` tokens in a window of ``size`` tokens.

    ``used`` can be more than ``size``: the user wrote a window that is too small, or the
    context is over the limit. The number is sent as it is.
    """
    return UsageUpdate(session_update="usage_update", used=used, size=size)


def _provider_tokens(message: object) -> int:
    """The tokens that a provider reported with an assistant message, or 0 when there are none."""
    if not isinstance(message, AssistantMessage) or message.stopReason in _NO_NUMBER:
        return 0
    return calculate_context_tokens(message.usage)


def turn_context_tokens(message: object, tool_results: Sequence[Any] = ()) -> int | None:
    """The tokens in the context at the end of one turn, or ``None`` when there is no number.

    ``message`` is the assistant message of the turn. The provider counted everything that it
    was sent, and the answer too. The tool results of the turn are not in that count yet: the
    next request carries them. They are added as an estimate.
    """
    reported = _provider_tokens(message)
    if reported <= 0:
        return None
    return reported + sum(estimate_message_tokens(result) for result in tool_results)


def estimated_context_tokens(messages: Sequence[Any]) -> int:
    """An estimate of the tokens in ``messages``, with no number of a provider.

    It is for the context after a compaction. The kept messages still carry the usage of the
    time before the compaction, and that usage counts a context that no longer exists.
    """
    return sum(estimate_message_tokens(message) for message in messages)


def stored_context_tokens(entries: Sequence[SessionTreeEntry]) -> int | None:
    """The tokens in the context of a saved session, or ``None`` when there is no number.

    ``entries`` are the entries of the branch, from the root to the leaf. This is for
    ``session/load`` and ``session/resume``.

    The number of a provider is true for the context that existed when the provider gave it. A
    compaction ends that context. So a number counts only when an assistant message that carries
    one comes after the last compaction. When there is none, there is nothing to say yet, and
    the next turn gives the number.
    """
    entries = list(entries)
    start = 0
    for index, entry in enumerate(entries):
        if isinstance(entry, CompactionEntry):
            start = index + 1
    after_last_compaction = build_session_context(entries[start:]).messages
    if not any(_provider_tokens(message) > 0 for message in after_last_compaction):
        return None
    return estimate_context_tokens(build_session_context(entries).messages)
