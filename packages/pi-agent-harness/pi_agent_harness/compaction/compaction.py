"""LLM-backed compaction and branch summarization."""

from __future__ import annotations

import json
from typing import Any

from pi_agent_core.messages import AssistantMessage, ToolResultMessage, UserMessage
from pi_agent_core.types import LlmContext, Model, StreamFn, StreamOptions
from pi_agent_harness.messages import BashExecutionMessage, BranchSummaryMessage, CustomMessage
from pi_agent_harness.session.session import build_session_context
from pi_agent_harness.types import (
    CompactionPreparation,
    CompactionResult,
    MessageEntry,
    SessionTreeEntry,
)

COMPACTION_SYSTEM_PROMPT = """You summarize an agent conversation for future continuation.

Return a concise but complete summary with these sections:
- Goal
- Constraints and preferences
- Progress
- Key decisions
- Next steps
- Critical context

Preserve concrete file paths, tool results, identifiers, and user preferences.
"""

BRANCH_SUMMARY_PREAMBLE = "Summary of abandoned branch:\n\n"


async def complete_simple(
    stream_fn: StreamFn,
    model: Model,
    prompt: str,
    options: StreamOptions | None = None,
) -> AssistantMessage:
    stream = await stream_fn(
        model,
        LlmContext(system_prompt=COMPACTION_SYSTEM_PROMPT, messages=[UserMessage(content=prompt)]),
        options or StreamOptions(),
    )
    return await stream.message_result()


async def compact_preparation(
    preparation: CompactionPreparation,
    stream_fn: StreamFn,
    model: Model,
    custom_instructions: str | None = None,
    options: StreamOptions | None = None,
) -> CompactionResult:
    prompt = build_compaction_prompt(preparation, custom_instructions)
    message = await complete_simple(stream_fn, model, prompt, options)
    summary = _assistant_text(message).strip()
    if preparation.splitTurnSummary:
        summary = f"{summary}\n\n{preparation.splitTurnSummary}".strip()
    details = extract_file_details(preparation.messages, preparation.previousDetails)
    return CompactionResult(
        summary=summary,
        firstKeptEntryId=preparation.firstKeptEntryId,
        tokensBefore=preparation.tokensBefore,
        details=details,
    )


async def create_branch_summary(
    entries: list[SessionTreeEntry],
    stream_fn: StreamFn,
    model: Model,
    custom_instructions: str | None = None,
    options: StreamOptions | None = None,
) -> CompactionResult | None:
    messages = build_session_context(entries).messages
    if not messages:
        return None
    prompt = build_branch_summary_prompt(messages, custom_instructions)
    message = await complete_simple(stream_fn, model, prompt, options)
    summary = BRANCH_SUMMARY_PREAMBLE + _assistant_text(message).strip()
    return CompactionResult(
        summary=summary,
        firstKeptEntryId=entries[-1].id,
        tokensBefore=sum(len(str(entry)) for entry in entries),
        details=extract_file_details(messages),
    )


def build_compaction_prompt(
    preparation: CompactionPreparation,
    custom_instructions: str | None = None,
) -> str:
    mode = "Update the existing summary" if preparation.previousSummary else "Create a summary"
    parts = [mode, ""]
    if preparation.previousSummary:
        parts.extend(["Existing summary:", preparation.previousSummary, ""])
    if custom_instructions:
        parts.extend(["Additional instructions:", custom_instructions, ""])
    parts.extend(["Conversation to summarize:", serialize_conversation(preparation.messages)])
    return "\n".join(parts)


def build_branch_summary_prompt(
    messages: list[Any],
    custom_instructions: str | None = None,
) -> str:
    parts = [
        "Summarize this abandoned branch so the agent can understand what was left behind.",
        "",
    ]
    if custom_instructions:
        parts.extend(["Additional instructions:", custom_instructions, ""])
    parts.extend(["Branch conversation:", serialize_conversation(messages)])
    return "\n".join(parts)


def serialize_conversation(messages: list[Any]) -> str:
    return "\n".join(_serialize_message(message) for message in messages)


def extract_file_details(messages: list[Any], previous_details: Any = None) -> dict[str, list[str]]:
    read_files = set(
        (previous_details or {}).get("readFiles", []) if isinstance(previous_details, dict) else []
    )
    modified_files = set(
        (previous_details or {}).get("modifiedFiles", [])
        if isinstance(previous_details, dict)
        else []
    )
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for block in message.content:
            if block.get("type") != "toolCall":
                continue
            path = block.get("arguments", {}).get("path")
            if not isinstance(path, str):
                continue
            name = block.get("name", "")
            if name in ("read", "list", "glob"):
                read_files.add(path)
            if name in ("write", "edit", "apply_patch"):
                modified_files.add(path)
    return {"readFiles": sorted(read_files), "modifiedFiles": sorted(modified_files)}


def collect_entries_for_branch_summary(
    entries: list[SessionTreeEntry],
    old_leaf_id: str | None,
    target_id: str | None,
) -> list[SessionTreeEntry]:
    old_path = _path_to_root(entries, old_leaf_id)
    target_path = _path_to_root(entries, target_id)
    target_ids = {entry.id for entry in target_path}
    divergence_idx = 0
    for idx, entry in enumerate(old_path):
        if entry.id not in target_ids:
            divergence_idx = idx
            break
    else:
        return []
    return [entry for entry in old_path[divergence_idx:] if _entry_role(entry) != "toolResult"]


def prepare_branch_entries(
    entries: list[SessionTreeEntry],
    token_budget: int,
) -> list[SessionTreeEntry]:
    selected: list[SessionTreeEntry] = []
    running = 0
    for entry in reversed(entries):
        if _entry_role(entry) == "toolResult":
            continue
        running += max(1, len(str(entry)) // 4)
        if running > token_budget and selected:
            break
        selected.insert(0, entry)
    return selected


def _path_to_root(entries: list[SessionTreeEntry], leaf_id: str | None) -> list[SessionTreeEntry]:
    if leaf_id is None:
        return []
    by_id = {entry.id: entry for entry in entries}
    current = by_id.get(leaf_id)
    path: list[SessionTreeEntry] = []
    while current is not None:
        path.insert(0, current)
        current = by_id.get(current.parentId) if current.parentId else None
    return path


def _entry_role(entry: SessionTreeEntry) -> str | None:
    if isinstance(entry, MessageEntry):
        return entry.message.get("role")
    return getattr(entry, "type", None)


def _serialize_message(message: Any) -> str:
    if isinstance(message, UserMessage):
        return f"[User]: {_content_text(message.content)}"
    if isinstance(message, AssistantMessage):
        parts = []
        for block in message.content:
            if block.get("type") == "text":
                parts.append(f"[Assistant]: {block.get('text', '')}")
            elif block.get("type") == "thinking":
                parts.append(f"[Assistant thinking]: {block.get('thinking', '')}")
            elif block.get("type") == "toolCall":
                args = json.dumps(block.get("arguments", {}), sort_keys=True)
                parts.append(f"[Assistant tool calls]: {block.get('name')}({args})")
        return "\n".join(parts) or "[Assistant]:"
    if isinstance(message, ToolResultMessage):
        return f"[Tool result]: {_content_text(message.content)}"
    if isinstance(message, BashExecutionMessage):
        return f"[Bash]: {message.command}\n{message.output[:2000]}"
    if isinstance(message, CustomMessage):
        return f"[Custom:{message.customType}]: {_content_text(message.content)}"
    if isinstance(message, BranchSummaryMessage):
        return f"[Branch summary]: {message.summary}"
    return str(message)[:2000]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content[:2000]
    if isinstance(content, list):
        text = " ".join(str(block.get("text", "[image]")) for block in content)
        return text[:2000]
    return str(content)[:2000]


def _assistant_text(message: AssistantMessage) -> str:
    return "".join(
        block.get("text", "") for block in message.content if block.get("type") == "text"
    )
