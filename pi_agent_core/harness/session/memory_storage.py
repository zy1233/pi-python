"""In-memory session storage for tests and ephemeral sessions."""

from __future__ import annotations

from typing import Any

from ..types import (
    LabelEntry,
    LeafEntry,
    SessionError,
    SessionMetadata,
    SessionTreeEntry,
)
from .session import iso_now
from .uuid7 import uuid7


def _leaf_id_after_entry(entry: SessionTreeEntry) -> str | None:
    return entry.targetId if isinstance(entry, LeafEntry) else entry.id


def _update_label_cache(labels_by_id: dict[str, str], entry: SessionTreeEntry) -> None:
    if not isinstance(entry, LabelEntry):
        return
    label = entry.label.strip() if entry.label else ""
    if label:
        labels_by_id[entry.targetId] = label
    else:
        labels_by_id.pop(entry.targetId, None)


def _generate_entry_id(by_id: dict[str, SessionTreeEntry]) -> str:
    for _ in range(100):
        candidate = uuid7()[:8]
        if candidate not in by_id:
            return candidate
    return uuid7()


class MemorySessionStorage:
    def __init__(
        self,
        metadata: SessionMetadata,
        entries: list[SessionTreeEntry] | None = None,
        leaf_id: str | None = None,
    ) -> None:
        self._metadata = metadata
        self._entries = list(entries or [])
        self._by_id = {entry.id: entry for entry in self._entries}
        self._labels_by_id: dict[str, str] = {}
        for entry in self._entries:
            _update_label_cache(self._labels_by_id, entry)
        self._current_leaf_id = leaf_id

    @classmethod
    async def create(cls, cwd: str = "", session_id: str | None = None) -> MemorySessionStorage:
        return cls(
            SessionMetadata(
                id=session_id or uuid7(),
                createdAt=iso_now(),
            )
        )

    async def get_metadata(self) -> SessionMetadata:
        return self._metadata

    async def get_leaf_id(self) -> str | None:
        if self._current_leaf_id is not None and self._current_leaf_id not in self._by_id:
            raise SessionError("invalid_session", f"Entry {self._current_leaf_id} not found")
        return self._current_leaf_id

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        if leaf_id is not None and leaf_id not in self._by_id:
            raise SessionError("not_found", f"Entry {leaf_id} not found")
        entry = LeafEntry(
            id=_generate_entry_id(self._by_id),
            parentId=self._current_leaf_id,
            timestamp=iso_now(),
            targetId=leaf_id,
        )
        await self.append_entry(entry)
        self._current_leaf_id = leaf_id

    async def create_entry_id(self) -> str:
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        if entry.id in self._by_id:
            raise SessionError("invalid_entry", f"Duplicate entry id {entry.id}")
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        _update_label_cache(self._labels_by_id, entry)
        self._current_leaf_id = _leaf_id_after_entry(entry)

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return self._by_id.get(id)

    async def find_entries(self, type_: str) -> list[SessionTreeEntry]:
        return [entry for entry in self._entries if entry.type == type_]

    async def get_label(self, id: str) -> str | None:
        return self._labels_by_id.get(id)

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        if leaf_id is None:
            return []
        path: list[SessionTreeEntry] = []
        current = self._by_id.get(leaf_id)
        if current is None:
            raise SessionError("not_found", f"Entry {leaf_id} not found")
        while current is not None:
            path.insert(0, current)
            if current.parentId is None:
                break
            parent = self._by_id.get(current.parentId)
            if parent is None:
                raise SessionError("invalid_session", f"Entry {current.parentId} not found")
            current = parent
        return path

    async def get_entries(self) -> list[SessionTreeEntry]:
        return list(self._entries)

    def clone(self, metadata: SessionMetadata | None = None) -> MemorySessionStorage:
        return MemorySessionStorage(
            metadata or self._metadata.model_copy(deep=True),
            [entry.model_copy(deep=True) for entry in self._entries],
            self._current_leaf_id,
        )

    @classmethod
    def from_entries(
        cls,
        metadata: SessionMetadata,
        entries: list[SessionTreeEntry],
        leaf_id: str | None = None,
    ) -> MemorySessionStorage:
        if leaf_id is None and entries:
            leaf_id = _leaf_id_after_entry(entries[-1])
        return cls(metadata, [entry.model_copy(deep=True) for entry in entries], leaf_id)


def session_metadata_from_options(options: dict[str, Any] | None) -> SessionMetadata:
    options = options or {}
    return SessionMetadata(id=options.get("id") or uuid7(), createdAt=iso_now())
