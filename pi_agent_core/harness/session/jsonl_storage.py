"""pi-v3-compatible append-only JSONL session storage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from ..types import (
    JsonlSessionMetadata,
    LabelEntry,
    LeafEntry,
    SessionError,
    SessionTreeEntry,
    SessionTreeEntryAdapter,
)
from .memory_storage import _generate_entry_id, _leaf_id_after_entry, _update_label_cache
from .session import iso_now


class JsonlStorageFs(Protocol):
    async def read_text_file(self, path: str) -> str: ...

    async def read_text_lines(self, path: str, max_lines: int | None = None) -> list[str]: ...

    async def write_file(self, path: str, content: str | bytes) -> None: ...

    async def append_file(self, path: str, content: str | bytes) -> None: ...


class _PathJsonlStorageFs:
    async def read_text_file(self, path: str) -> str:
        return await asyncio.to_thread(Path(path).read_text, encoding="utf-8")

    async def read_text_lines(self, path: str, max_lines: int | None = None) -> list[str]:
        def read() -> list[str]:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            return lines[:max_lines] if max_lines is not None else lines

        return await asyncio.to_thread(read)

    async def write_file(self, path: str, content: str | bytes) -> None:
        def write() -> None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content, encoding="utf-8")

        await asyncio.to_thread(write)

    async def append_file(self, path: str, content: str | bytes) -> None:
        def append() -> None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "ab" if isinstance(content, bytes) else "a"
            kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
            with p.open(mode, **kwargs) as f:
                f.write(content)

        await asyncio.to_thread(append)


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _invalid_session(file_path: str, message: str, cause: Exception | None = None) -> SessionError:
    return SessionError(
        "invalid_session", f"Invalid JSONL session file {file_path}: {message}", cause
    )


def _invalid_entry(
    file_path: str,
    line_number: int,
    message: str,
    cause: Exception | None = None,
) -> SessionError:
    return SessionError(
        "invalid_entry",
        f"Invalid JSONL session file {file_path}: line {line_number} {message}",
        cause,
    )


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _parse_header_line(line: str, file_path: str) -> dict[str, Any]:
    try:
        parsed = json.loads(line)
    except Exception as e:
        raise _invalid_session(file_path, "first line is not a valid session header", e) from e
    if not _is_record(parsed) or parsed.get("type") != "session":
        raise _invalid_session(file_path, "first line is not a valid session header")
    if parsed.get("version") != 3:
        raise _invalid_session(file_path, "unsupported session version")
    if not isinstance(parsed.get("id"), str) or not parsed["id"]:
        raise _invalid_session(file_path, "session header is missing id")
    if not isinstance(parsed.get("timestamp"), str) or not parsed["timestamp"]:
        raise _invalid_session(file_path, "session header is missing timestamp")
    if not isinstance(parsed.get("cwd"), str) or not parsed["cwd"]:
        raise _invalid_session(file_path, "session header is missing cwd")
    if parsed.get("parentSession") is not None and not isinstance(parsed["parentSession"], str):
        raise _invalid_session(file_path, "session header parentSession must be a string")
    return parsed


def _header_to_metadata(header: dict[str, Any], path: str) -> JsonlSessionMetadata:
    return JsonlSessionMetadata(
        id=header["id"],
        createdAt=header["timestamp"],
        cwd=header["cwd"],
        path=path,
        parentSessionPath=header.get("parentSession"),
    )


def _entry_to_json(entry: SessionTreeEntry) -> str:
    data = entry.model_dump(exclude_none=True)
    # pi v3 entries always carry parentId, even for root entries where it is
    # null. Do not let exclude_none remove it.
    data["parentId"] = entry.parentId
    if isinstance(entry, LeafEntry):
        data["targetId"] = entry.targetId
    return _json_dumps(data)


class JsonlSessionStorage:
    def __init__(
        self,
        fs: JsonlStorageFs,
        file_path: str,
        header: dict[str, Any],
        entries: list[SessionTreeEntry],
        leaf_id: str | None,
    ) -> None:
        self._fs = fs
        self._file_path = file_path
        self._metadata = _header_to_metadata(header, file_path)
        self._entries = entries
        self._by_id = {entry.id: entry for entry in entries}
        self._labels_by_id: dict[str, str] = {}
        for entry in entries:
            _update_label_cache(self._labels_by_id, entry)
        self._current_leaf_id = leaf_id

    @staticmethod
    def parse_header_line(line: str, file_path: str) -> dict[str, Any]:
        return _parse_header_line(line, file_path)

    @staticmethod
    def parse_entry_line(line: str, file_path: str, line_number: int) -> SessionTreeEntry:
        try:
            parsed = json.loads(line)
        except Exception as e:
            raise _invalid_entry(file_path, line_number, "is not valid JSON", e) from e
        if not _is_record(parsed):
            raise _invalid_entry(file_path, line_number, "is not a valid session entry")
        if not isinstance(parsed.get("type"), str):
            raise _invalid_entry(file_path, line_number, "is missing entry type")
        if not isinstance(parsed.get("id"), str) or not parsed["id"]:
            raise _invalid_entry(file_path, line_number, "is missing entry id")
        if parsed.get("parentId") is not None and not isinstance(parsed["parentId"], str):
            raise _invalid_entry(file_path, line_number, "has invalid parentId")
        if not isinstance(parsed.get("timestamp"), str) or not parsed["timestamp"]:
            raise _invalid_entry(file_path, line_number, "is missing timestamp")
        if (
            parsed["type"] == "leaf"
            and parsed.get("targetId") is not None
            and not isinstance(parsed["targetId"], str)
        ):
            raise _invalid_entry(file_path, line_number, "has invalid targetId")
        try:
            return SessionTreeEntryAdapter.validate_python(parsed)
        except Exception as e:
            raise _invalid_entry(file_path, line_number, f"has invalid shape: {e}", e) from e

    @classmethod
    async def open(
        cls,
        fs: JsonlStorageFs,
        file_path: str,
    ) -> JsonlSessionStorage:
        content = await fs.read_text_file(file_path)
        lines = [line for line in content.splitlines() if line.strip()]
        if not lines:
            raise _invalid_session(file_path, "missing session header")
        header = _parse_header_line(lines[0], file_path)
        entries: list[SessionTreeEntry] = []
        leaf_id: str | None = None
        for index, line in enumerate(lines[1:], start=2):
            entry = cls.parse_entry_line(line, file_path, index)
            entries.append(entry)
            leaf_id = _leaf_id_after_entry(entry)
        return cls(fs, file_path, header, entries, leaf_id)

    @classmethod
    async def open_path(cls, file_path: str | Path) -> JsonlSessionStorage:
        return await cls.open(_PathJsonlStorageFs(), str(file_path))

    @classmethod
    async def create(
        cls,
        fs: JsonlStorageFs,
        file_path: str,
        *,
        cwd: str,
        session_id: str,
        parent_session_path: str | None = None,
    ) -> JsonlSessionStorage:
        header: dict[str, Any] = {
            "type": "session",
            "version": 3,
            "id": session_id,
            "timestamp": iso_now(),
            "cwd": cwd,
        }
        if parent_session_path is not None:
            header["parentSession"] = parent_session_path
        await fs.write_file(file_path, f"{_json_dumps(header)}\n")
        return cls(fs, file_path, header, [], None)

    @classmethod
    async def create_path(
        cls,
        file_path: str | Path,
        *,
        cwd: str,
        session_id: str,
        parent_session_path: str | None = None,
    ) -> JsonlSessionStorage:
        return await cls.create(
            _PathJsonlStorageFs(),
            str(file_path),
            cwd=cwd,
            session_id=session_id,
            parent_session_path=parent_session_path,
        )

    async def get_metadata(self) -> JsonlSessionMetadata:
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
        await self._fs.append_file(self._file_path, f"{_entry_to_json(entry)}\n")
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        if isinstance(entry, LabelEntry):
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


async def load_jsonl_session_metadata(
    fs: JsonlStorageFs,
    file_path: str,
) -> JsonlSessionMetadata:
    lines = await fs.read_text_lines(file_path, max_lines=1)
    line = lines[0] if lines else ""
    if line.strip():
        return _header_to_metadata(_parse_header_line(line, file_path), file_path)
    raise _invalid_session(file_path, "missing session header")
