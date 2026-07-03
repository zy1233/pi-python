"""In-memory session repository."""

from __future__ import annotations

from typing import Any

from ..types import SessionError, SessionMetadata
from .memory_storage import (
    MemorySessionStorage,
    _leaf_id_after_entry,
    session_metadata_from_options,
)
from .repo_utils import get_entries_to_fork
from .session import Session


class MemorySessionRepo:
    def __init__(self) -> None:
        self._storages: dict[str, MemorySessionStorage] = {}

    async def create(self, options: dict[str, Any] | None = None) -> Session:
        metadata = session_metadata_from_options(options)
        if metadata.id in self._storages:
            raise SessionError("invalid_session", f"Session {metadata.id} already exists")
        storage = MemorySessionStorage(metadata)
        self._storages[metadata.id] = storage
        return Session(storage)

    async def open(self, metadata: SessionMetadata) -> Session:
        storage = self._storages.get(metadata.id)
        if storage is None:
            raise SessionError("not_found", f"Session {metadata.id} not found")
        return Session(storage)

    async def list(self, options: dict[str, Any] | None = None) -> list[SessionMetadata]:
        return [await storage.get_metadata() for storage in self._storages.values()]

    async def delete(self, metadata: SessionMetadata) -> None:
        if self._storages.pop(metadata.id, None) is None:
            raise SessionError("not_found", f"Session {metadata.id} not found")

    async def fork(
        self,
        source: SessionMetadata,
        options: dict[str, Any] | None = None,
    ) -> Session:
        options = options or {}
        source_storage = self._storages.get(source.id)
        if source_storage is None:
            raise SessionError("not_found", f"Session {source.id} not found")
        entries = await get_entries_to_fork(source_storage, options)
        metadata = session_metadata_from_options(options)
        if metadata.id in self._storages:
            raise SessionError("invalid_session", f"Session {metadata.id} already exists")
        leaf_id = _leaf_id_after_entry(entries[-1]) if entries else None
        storage = MemorySessionStorage.from_entries(metadata, entries, leaf_id)
        self._storages[metadata.id] = storage
        return Session(storage)
