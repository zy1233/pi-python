"""Append-only JSONL journal for workflow run replay.

Records each host call (``agent()``, ``phase()``, ``log()``) with a SHA-256
request hash so that re-running the same workflow can skip LLM calls whose
inputs have not changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ENTRIES = 10_000
MAX_FILE_BYTES = 64 * 1024 * 1024  # 64 MB

_MISS = object()


def _deterministic_json(obj: Any) -> str:
    """JSON-serialise *obj* with sorted keys for stable hashing."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def hash_request(kind: str, params: Any) -> str:
    """SHA-256 hex digest of ``kind`` + deterministic JSON of *params*."""
    payload = _deterministic_json({"kind": kind, "params": params})
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class JournalEntry:
    seq: int
    kind: str
    req_hash: str
    result: Any
    at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class Journal:
    """Append-only JSONL journal for deterministic replay.

    * ``try_replay(kind, req_hash)`` — returns cached result on hash match,
      or ``_MISS`` on divergence (auto-truncates).
    * ``append(kind, req_hash, result)`` — records a new entry.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._entries: list[JournalEntry] = []
        self._path = path
        self._cursor = 0

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Journal:
        """Load an existing journal file (one JSON object per line)."""
        j = cls(path)
        if not path.exists():
            return j
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    raw = json.loads(line)
                    j._entries.append(
                        JournalEntry(
                            seq=raw["seq"],
                            kind=raw["kind"],
                            req_hash=raw["req_hash"],
                            result=raw.get("result"),
                            at_ms=raw.get("at_ms", 0),
                        )
                    )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("Journal load failed (%s); starting fresh", exc)
            j._entries.clear()
        return j

    def _flush_to_disk(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    # -- replay / record -----------------------------------------------------

    def try_replay(self, kind: str, req_hash: str) -> Any:
        """Return cached result if the next entry matches, else ``_MISS``.

        On hash mismatch the journal is truncated from the current cursor
        position so subsequent calls will record fresh entries.
        """
        if self._cursor >= len(self._entries):
            return _MISS
        entry = self._entries[self._cursor]
        if entry.kind == kind and entry.req_hash == req_hash:
            self._cursor += 1
            return entry.result
        self.truncate_from(self._cursor)
        return _MISS

    def append(self, kind: str, req_hash: str, result: Any) -> None:
        """Record a new host-call result."""
        if len(self._entries) >= MAX_ENTRIES:
            logger.warning("Journal entry cap (%d) reached; skipping append", MAX_ENTRIES)
            return
        entry = JournalEntry(
            seq=len(self._entries),
            kind=kind,
            req_hash=req_hash,
            result=result,
        )
        line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            current_size = self._path.stat().st_size if self._path.exists() else 0
            if current_size + len(line.encode("utf-8")) > MAX_FILE_BYTES:
                logger.warning(
                    "Journal file size limit (%d bytes) reached; skipping append",
                    MAX_FILE_BYTES,
                )
                return
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line)
        self._entries.append(entry)
        self._cursor = len(self._entries)

    def truncate_from(self, seq: int) -> None:
        """Discard entries from *seq* onwards (divergence)."""
        if seq < len(self._entries):
            self._entries = self._entries[:seq]
            self._cursor = seq
            self._flush_to_disk()

    # -- introspection -------------------------------------------------------

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def replayed_count(self) -> int:
        return self._cursor
