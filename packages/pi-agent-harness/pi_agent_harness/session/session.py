"""Session tree facade and context replay."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from pi_agent_core.messages import AssistantMessage, ToolResultMessage, UserMessage
from pi_agent_core.types import AgentMessage
from pi_agent_harness.messages import (
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
)

from ..types import (
    ActiveToolsChangeEntry,
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionContext,
    SessionError,
    SessionInfoEntry,
    SessionMetadata,
    SessionStorage,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
)


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_to_ms(timestamp: str) -> int:
    normalized = timestamp.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


def _message_to_dict(message: AgentMessage) -> dict[str, Any]:
    if isinstance(message, BaseModel):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return dict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    raise SessionError("invalid_entry", f"Cannot persist message of type {type(message).__name__}")


def _message_from_raw(raw: Any) -> AgentMessage:
    if isinstance(raw, UserMessage | AssistantMessage | ToolResultMessage):
        return raw
    if isinstance(raw, BaseModel):
        return raw
    if not isinstance(raw, dict):
        return raw
    role = raw.get("role")
    if role == "user":
        return UserMessage.model_validate(raw)
    if role == "assistant":
        return AssistantMessage.model_validate(raw)
    if role == "toolResult":
        return ToolResultMessage.model_validate(raw)
    return raw


def create_branch_summary_message(
    summary: str, from_id: str, timestamp: str
) -> BranchSummaryMessage:
    return BranchSummaryMessage(summary=summary, fromId=from_id, timestamp=iso_to_ms(timestamp))


def create_compaction_summary_message(
    summary: str, tokens_before: int, timestamp: str
) -> CompactionSummaryMessage:
    return CompactionSummaryMessage(
        summary=summary,
        tokensBefore=tokens_before,
        timestamp=iso_to_ms(timestamp),
    )


def create_custom_message(entry: CustomMessageEntry) -> CustomMessage:
    return CustomMessage(
        customType=entry.customType,
        content=entry.content,
        display=entry.display,
        details=entry.details,
        timestamp=iso_to_ms(entry.timestamp),
    )


def _append_message_from_entry(messages: list[AgentMessage], entry: SessionTreeEntry) -> None:
    if isinstance(entry, MessageEntry):
        messages.append(_message_from_raw(entry.message))
    elif isinstance(entry, CustomMessageEntry):
        messages.append(create_custom_message(entry))
    elif isinstance(entry, BranchSummaryEntry) and entry.summary:
        messages.append(create_branch_summary_message(entry.summary, entry.fromId, entry.timestamp))


def build_session_context(path_entries: list[SessionTreeEntry]) -> SessionContext:
    thinking_level = "off"
    model: dict[str, str] | None = None
    active_tool_names: list[str] | None = None
    compaction: CompactionEntry | None = None

    for entry in path_entries:
        if isinstance(entry, ThinkingLevelChangeEntry):
            thinking_level = entry.thinkingLevel
        elif isinstance(entry, ModelChangeEntry):
            model = {"provider": entry.provider, "modelId": entry.modelId}
        elif isinstance(entry, MessageEntry) and entry.message.get("role") == "assistant":
            provider = entry.message.get("provider")
            model_id = entry.message.get("model")
            if isinstance(provider, str) and isinstance(model_id, str):
                model = {"provider": provider, "modelId": model_id}
        elif isinstance(entry, ActiveToolsChangeEntry):
            active_tool_names = list(entry.activeToolNames)
        elif isinstance(entry, CompactionEntry):
            compaction = entry

    messages: list[AgentMessage] = []
    if compaction is not None:
        messages.append(
            create_compaction_summary_message(
                compaction.summary,
                compaction.tokensBefore,
                compaction.timestamp,
            )
        )
        compaction_idx = next(
            (
                i
                for i, entry in enumerate(path_entries)
                if isinstance(entry, CompactionEntry) and entry.id == compaction.id
            ),
            -1,
        )
        found_first_kept = False
        for entry in path_entries[:compaction_idx]:
            if entry.id == compaction.firstKeptEntryId:
                found_first_kept = True
            if found_first_kept:
                _append_message_from_entry(messages, entry)
        for entry in path_entries[compaction_idx + 1 :]:
            _append_message_from_entry(messages, entry)
    else:
        for entry in path_entries:
            _append_message_from_entry(messages, entry)

    return SessionContext(
        messages=messages,
        thinkingLevel=thinking_level,
        model=model,
        activeToolNames=active_tool_names,
    )


class Session:
    def __init__(self, storage: SessionStorage) -> None:
        self._storage = storage

    async def get_metadata(self) -> SessionMetadata:
        return await self._storage.get_metadata()

    def get_storage(self) -> SessionStorage:
        return self._storage

    async def get_leaf_id(self) -> str | None:
        return await self._storage.get_leaf_id()

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return await self._storage.get_entry(id)

    async def get_entries(self) -> list[SessionTreeEntry]:
        return await self._storage.get_entries()

    async def get_branch(self, from_id: str | None = None) -> list[SessionTreeEntry]:
        leaf_id = from_id if from_id is not None else await self._storage.get_leaf_id()
        return await self._storage.get_path_to_root(leaf_id)

    async def build_context(self) -> SessionContext:
        return build_session_context(await self.get_branch())

    async def get_label(self, id: str) -> str | None:
        return await self._storage.get_label(id)

    async def get_session_name(self) -> str | None:
        entries = await self._storage.find_entries("session_info")
        if not entries:
            return None
        latest = entries[-1]
        if not isinstance(latest, SessionInfoEntry) or not latest.name:
            return None
        # pi: `entries.at(-1)?.name?.trim() || undefined` - whitespace-only
        # names read back as absent, not as "".
        return latest.name.strip() or None

    async def _append_typed_entry(self, entry: SessionTreeEntry) -> str:
        await self._storage.append_entry(entry)
        return entry.id

    async def _new_entry_base(self) -> dict[str, Any]:
        return {
            "id": await self._storage.create_entry_id(),
            "parentId": await self._storage.get_leaf_id(),
            "timestamp": iso_now(),
        }

    async def append_message(self, message: AgentMessage) -> str:
        return await self._append_typed_entry(
            MessageEntry(**await self._new_entry_base(), message=_message_to_dict(message))
        )

    async def append_thinking_level_change(self, thinking_level: str) -> str:
        return await self._append_typed_entry(
            ThinkingLevelChangeEntry(**await self._new_entry_base(), thinkingLevel=thinking_level)
        )

    async def append_model_change(self, provider: str, model_id: str) -> str:
        return await self._append_typed_entry(
            ModelChangeEntry(**await self._new_entry_base(), provider=provider, modelId=model_id)
        )

    async def append_active_tools_change(self, active_tool_names: list[str]) -> str:
        return await self._append_typed_entry(
            ActiveToolsChangeEntry(
                **await self._new_entry_base(),
                activeToolNames=list(active_tool_names),
            )
        )

    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any = None,
        from_hook: bool | None = None,
    ) -> str:
        return await self._append_typed_entry(
            CompactionEntry(
                **await self._new_entry_base(),
                summary=summary,
                firstKeptEntryId=first_kept_entry_id,
                tokensBefore=tokens_before,
                details=details,
                fromHook=from_hook,
            )
        )

    async def append_custom_entry(self, custom_type: str, data: Any = None) -> str:
        return await self._append_typed_entry(
            CustomEntry(**await self._new_entry_base(), customType=custom_type, data=data)
        )

    async def append_custom_message_entry(
        self,
        custom_type: str,
        content: str | list[dict[str, Any]],
        display: bool,
        details: Any = None,
    ) -> str:
        return await self._append_typed_entry(
            CustomMessageEntry(
                **await self._new_entry_base(),
                customType=custom_type,
                content=content,
                display=display,
                details=details,
            )
        )

    async def append_label(self, target_id: str, label: str | None) -> str:
        if not await self._storage.get_entry(target_id):
            raise SessionError("not_found", f"Entry {target_id} not found")
        return await self._append_typed_entry(
            LabelEntry(**await self._new_entry_base(), targetId=target_id, label=label)
        )

    async def append_session_name(self, name: str) -> str:
        # pi only folds newlines into spaces (`name.replace(/[\r\n]+/g, " ")`);
        # other whitespace runs (tabs, doubled spaces) are preserved.
        sanitized = re.sub(r"[\r\n]+", " ", name).strip()
        return await self._append_typed_entry(
            SessionInfoEntry(**await self._new_entry_base(), name=sanitized)
        )

    async def move_to(
        self,
        entry_id: str | None,
        summary: dict[str, Any] | None = None,
    ) -> str | None:
        if entry_id is not None and not await self._storage.get_entry(entry_id):
            raise SessionError("not_found", f"Entry {entry_id} not found")
        await self._storage.set_leaf_id(entry_id)
        if not summary:
            return None
        return await self._append_typed_entry(
            BranchSummaryEntry(
                id=await self._storage.create_entry_id(),
                parentId=entry_id,
                timestamp=iso_now(),
                # pi writes the move target (`entryId ?? "root"`); the summary
                # dict cannot override it, keeping persisted files identical to
                # pi's for the same navigation.
                fromId=entry_id if entry_id is not None else "root",
                summary=str(summary.get("summary", "")),
                details=summary.get("details"),
                fromHook=summary.get("fromHook"),
            )
        )
