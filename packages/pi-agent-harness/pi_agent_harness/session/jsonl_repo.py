"""Filesystem-backed JSONL session repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..env import LocalExecutionEnv
from ..types import FileError, FileInfo, JsonlSessionMetadata, SessionError
from .jsonl_storage import JsonlSessionStorage, JsonlStorageFs, load_jsonl_session_metadata
from .repo_utils import get_entries_to_fork
from .session import Session, iso_now, iso_to_ms
from .uuid7 import uuid7


class JsonlRepoFs(JsonlStorageFs, Protocol):
    """Narrow filesystem surface required by `JsonlSessionRepo`.

    `remove` must have force semantics (removing a missing path is a no-op),
    matching pi's `fs.remove(path, { force: true })`.
    """

    async def exists(self, path: str | Path) -> bool: ...

    async def list_dir(self, path: str | Path) -> list[FileInfo]: ...

    async def remove(self, path: str | Path) -> None: ...


def _created_at_ms(created_at: str) -> float:
    # Foreign files can carry non-ISO timestamps; JS `new Date(...)` yields NaN
    # there instead of throwing, so map unparseable values to a stable fallback.
    try:
        return iso_to_ms(created_at)
    except ValueError:
        return 0.0


def _safe_filename_timestamp(timestamp: str) -> str:
    return (
        timestamp.replace(":", "")
        .replace("-", "")
        .replace(".", "")
        .replace("+", "")
        .replace("Z", "Z")
    )


class JsonlSessionRepo:
    def __init__(self, directory: str | Path, fs: JsonlRepoFs | None = None) -> None:
        self._directory = Path(directory)
        self._fs = fs if fs is not None else LocalExecutionEnv(Path.cwd())

    def _session_path(self, session_id: str, created_at: str) -> Path:
        return self._directory / f"{_safe_filename_timestamp(created_at)}-{session_id}.jsonl"

    async def create(self, options: dict[str, Any] | None = None) -> Session:
        options = options or {}
        session_id = options.get("id") or uuid7()
        cwd = options.get("cwd") or str(Path.cwd())
        created_at = iso_now()
        path = self._session_path(session_id, created_at)
        if await self._fs.exists(path):
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
        if not await self._fs.exists(metadata.path):
            raise SessionError("not_found", f"Session file {metadata.path} not found")
        return Session(await JsonlSessionStorage.open(self._fs, metadata.path))

    async def list(self, options: dict[str, Any] | None = None) -> list[JsonlSessionMetadata]:
        options = options or {}

        try:
            entries = await self._fs.list_dir(str(self._directory))
        except FileError as exc:
            if exc.code == "not_found":
                return []
            raise

        result: list[JsonlSessionMetadata] = []
        for info in sorted(
            (
                entry
                for entry in entries
                if entry.kind != "directory" and entry.name.endswith(".jsonl")
            ),
            key=lambda entry: entry.name,
        ):
            path = str(self._directory / info.name)
            try:
                metadata = await load_jsonl_session_metadata(self._fs, path)
            except SessionError as exc:
                # pi skips files that fail header validation so a stray file in
                # the directory does not break the whole listing; other codes
                # (not_found/storage) still propagate.
                if exc.code == "invalid_session":
                    continue
                raise
            if options.get("cwd") is not None and metadata.cwd != options["cwd"]:
                continue
            result.append(metadata)
        # pi sorts newest-first by header createdAt.
        result.sort(key=lambda metadata: _created_at_ms(metadata.createdAt), reverse=True)
        return result

    async def delete(self, metadata: JsonlSessionMetadata) -> None:
        # pi deletes with `force: true`: deleting a missing session is a no-op.
        await self._fs.remove(metadata.path)

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
