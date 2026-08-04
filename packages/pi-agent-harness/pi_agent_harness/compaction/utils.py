"""Token estimation and cut-point selection for session compaction."""

from __future__ import annotations

import json
from typing import Any

from pi_agent_core.messages import AssistantMessage, ToolResultMessage, Usage, UserMessage
from pi_agent_core.types import AgentMessage
from pi_agent_harness.messages import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
)
from pi_agent_harness.session.session import build_session_context
from pi_agent_harness.types import (
    BranchSummaryEntry,
    CompactionEntry,
    CompactionPreparation,
    CompactionSettings,
    CustomMessageEntry,
    MessageEntry,
    SessionTreeEntry,
)


def calculate_context_tokens(usage: Usage | None) -> int:
    if usage is None:
        return 0
    if usage.totalTokens:
        return usage.totalTokens
    return usage.input + usage.output + usage.cacheRead + usage.cacheWrite


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def estimate_message_tokens(message: AgentMessage) -> int:
    role = getattr(message, "role", None)
    if isinstance(message, UserMessage):
        return _estimate_content_tokens(message.content)
    if isinstance(message, AssistantMessage):
        tokens = 0
        for block in message.content:
            kind = block.get("type")
            if kind == "text":
                tokens += estimate_tokens(str(block.get("text", "")))
            elif kind == "thinking":
                tokens += estimate_tokens(str(block.get("thinking", "")))
            elif kind == "toolCall":
                tokens += estimate_tokens(block.get("name", ""))
                tokens += estimate_tokens(json.dumps(block.get("arguments", {}), sort_keys=True))
        return tokens
    if isinstance(message, ToolResultMessage):
        return _estimate_content_tokens(message.content)
    if isinstance(message, BashExecutionMessage):
        return estimate_tokens(message.command) + estimate_tokens(message.output)
    if isinstance(message, CustomMessage):
        return _estimate_content_tokens(message.content)
    if isinstance(message, BranchSummaryMessage | CompactionSummaryMessage):
        return estimate_tokens(message.summary)
    if isinstance(message, dict):
        return _estimate_content_tokens(message.get("content", "")) + estimate_tokens(
            str(message.get("summary", ""))
        )
    return estimate_tokens(str(role or message))


def estimate_context_tokens(messages: list[AgentMessage]) -> int:
    total = 0
    anchor_idx: int | None = None
    anchor_tokens = 0
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        if isinstance(message, AssistantMessage) and message.stopReason not in ("error", "aborted"):
            tokens = calculate_context_tokens(message.usage)
            if tokens > 0:
                anchor_idx = idx
                anchor_tokens = tokens
                break
    if anchor_idx is not None:
        total += anchor_tokens
        messages = messages[anchor_idx + 1 :]
    for message in messages:
        total += estimate_message_tokens(message)
    return total


def should_compact(
    context_tokens: int,
    context_window: int | None,
    settings: CompactionSettings,
) -> bool:
    if not settings.enabled or context_window is None:
        return False
    return context_tokens > context_window - settings.reserve_tokens


def prepare_compaction(
    branch_entries: list[SessionTreeEntry],
    settings: CompactionSettings,
) -> CompactionPreparation | None:
    if not settings.enabled:
        return None
    message_entries = [entry for entry in branch_entries if _entry_to_message(entry) is not None]
    if len(message_entries) < 2:
        return None
    context = build_session_context(branch_entries)
    tokens_before = estimate_context_tokens(context.messages)
    first_kept = _select_first_kept_entry(branch_entries, settings.keep_recent_tokens)
    if first_kept is None:
        return None
    first_kept_idx = branch_entries.index(first_kept)
    if first_kept_idx <= 0:
        return None
    split_summary = _create_split_turn_summary(branch_entries, first_kept_idx)
    previous = next(
        (
            entry
            for entry in reversed(branch_entries[:first_kept_idx])
            if isinstance(entry, CompactionEntry)
        ),
        None,
    )
    return CompactionPreparation(
        entries=branch_entries[:first_kept_idx],
        messages=build_session_context(branch_entries[:first_kept_idx]).messages,
        keptMessages=build_session_context(branch_entries[first_kept_idx:]).messages,
        firstKeptEntryId=first_kept.id,
        tokensBefore=tokens_before,
        splitTurnSummary=split_summary,
        previousSummary=previous.summary if previous else None,
        previousDetails=previous.details if previous else None,
    )


def _estimate_content_tokens(content: Any) -> int:
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if block.get("type") == "text":
                total += estimate_tokens(str(block.get("text", "")))
            elif block.get("type") == "image":
                total += estimate_tokens(str(block.get("data", "")) or ("x" * 4800))
        return total
    return estimate_tokens(str(content))


def _entry_to_message(entry: SessionTreeEntry) -> AgentMessage | None:
    if isinstance(entry, MessageEntry):
        return build_session_context([entry]).messages[0]
    if isinstance(entry, CustomMessageEntry | BranchSummaryEntry):
        return build_session_context([entry]).messages[0]
    return None


def _is_message_bearing_entry(entry: SessionTreeEntry) -> bool:
    return isinstance(entry, MessageEntry | CustomMessageEntry | BranchSummaryEntry)


def _is_legal_cut_entry(entry: SessionTreeEntry) -> bool:
    if isinstance(entry, CustomMessageEntry | BranchSummaryEntry):
        return True
    if isinstance(entry, MessageEntry):
        role = entry.message.get("role")
        return role in (
            "user",
            "assistant",
            "bashExecution",
            "custom",
            "branchSummary",
            "compactionSummary",
        )
    return False


def _select_first_kept_entry(
    entries: list[SessionTreeEntry],
    keep_recent_tokens: int,
) -> SessionTreeEntry | None:
    running = 0
    candidate_idx: int | None = None
    for idx in range(len(entries) - 1, -1, -1):
        message = _entry_to_message(entries[idx])
        if message is not None:
            running += estimate_message_tokens(message)
        if running >= keep_recent_tokens:
            candidate_idx = idx
            break
    if candidate_idx is None:
        return None
    cut_idx: int | None = None
    for idx in range(candidate_idx, len(entries)):
        if _is_legal_cut_entry(entries[idx]):
            cut_idx = idx
            break
    if cut_idx is None:
        return None
    while cut_idx > 0 and not _is_message_bearing_entry(entries[cut_idx - 1]):
        cut_idx -= 1
    return entries[cut_idx]


def _create_split_turn_summary(entries: list[SessionTreeEntry], first_kept_idx: int) -> str | None:
    from pi_agent_harness.compaction.compaction import serialize_conversation

    first_kept = entries[first_kept_idx]
    if isinstance(first_kept, MessageEntry) and first_kept.message.get("role") == "user":
        return None
    for idx in range(first_kept_idx - 1, -1, -1):
        entry = entries[idx]
        if isinstance(entry, BranchSummaryEntry | CustomMessageEntry):
            start_idx = idx
            break
        if isinstance(entry, MessageEntry) and entry.message.get("role") in (
            "user",
            "bashExecution",
        ):
            start_idx = idx
            break
    else:
        return None
    messages = build_session_context(entries[start_idx:first_kept_idx]).messages
    if not messages:
        return None
    return "Turn Context:\n" + serialize_conversation(messages)
