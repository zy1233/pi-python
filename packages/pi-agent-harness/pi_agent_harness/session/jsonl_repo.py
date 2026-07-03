"""Filesystem-backed JSONL session repository."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..types import JsonlSessionMetadata, SessionError
from .jsonl_storage import (
    JsonlSessionStorage,
    _PathJsonlStorageFs,
    load_jsonl_session_metadata,
)
from .repo_utils import get_entries_to_fork
from .session import Session, iso_now
from .uuid7 import uuid7


def _safe_filename_timestamp(timestamp: str) -> str:
    return (
        timestamp.replace(":", "")
        .replace("-", "")
        .replace(".", "")
        .replace("+", "")
        .replace("Z", "Z")
    )


class JsonlSessionRepo:
    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._fs = _PathJsonlStorageFs()

    def _session_path(self, session_id: str, created_at: str) -> Path:
        return self._directory / f"{_safe_filename_timestamp(created_at)}-{session_id}.jsonl"

    async def create(self, options: dict[str, Any] | None = None) -> Session:
        options = options or {}
        session_id = options.get("id") or uuid7()
        cwd = options.get("cwd") or str(Path.cwd())
        created_at = iso_now()
        path = self._session_path(session_id, created_at)
        if path.exists():
            raise SessionError("invalid_session", f"Session file {path} already exists")
        storage = await JsonlSessionStorage.create(
            self._fs,
            str(path),
            cwd=cwd,
            session_id=session_id,
            parent_session_path=options.get("parentSessionPath"),
        )
        return Session(storage)

    async def open(self, metadata: JsonlSessionMetadata) -> Session:
        if not Path(metadata.path).exists():
            raise SessionError("not_found", f"Session file {metadata.path} not found")
        return Session(await JsonlSessionStorage.open(self._fs, metadata.path))

    async def list(self, options: dict[str, Any] | None = None) -> list[JsonlSessionMetadata]:
        options = options or {}

        def files() -> list[Path]:
            if not self._directory.exists():
                return []
            return sorted(self._directory.glob("*.jsonl"))

        result: list[JsonlSessionMetadata] = []
        for path in await asyncio.to_thread(files):
            metadata = await load_jsonl_session_metadata(self._fs, str(path))
            if options.get("cwd") is not None and metadata.cwd != options["cwd"]:
                continue
            result.append(metadata)
        return result

    async def delete(self, metadata: JsonlSessionMetadata) -> None:
        path = Path(metadata.path)
        if not path.exists():
            raise SessionError("not_found", f"Session file {metadata.path} not found")
        await asyncio.to_thread(path.unlink)

    async def fork(
        self,
        source: JsonlSessionMetadata,
        options: dict[str, Any] | None = None,
    ) -> Session:
        options = options or {}
        source_session = await self.open(source)
        forked_entries = await get_entries_to_fork(source_session.get_storage(), options)

        fork = await self.create(
            {
                "id": options.get("id") or uuid7(),
                "cwd": options.get("cwd") or source.cwd,
                "parentSessionPath": source.path,
            }
        )
        for entry in forked_entries:
            await fork.get_storage().append_entry(entry.model_copy(deep=True))
        return fork
