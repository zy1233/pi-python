"""Append-only JSONL journal for workflow run replay.

Records the result of each ``agent()`` call under a SHA-256 hash of its request, so that
re-running a workflow can skip the calls it already has an answer to.

The journal is a request-keyed cache, not a transcript. A call is answered by the earliest
recorded answer to the *same* request that this run has not used yet, whatever order the
answers were recorded in (``parallel()`` records in completion order) and whatever else in
the script changed. Two things follow: identical requests replay in the order they were
recorded, and a step whose request is unchanged is replayed even when an earlier step had to
run again, so what a replayed step did to the workspace (files it edited) is not redone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
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
    """Append-only JSONL journal for replay (see the module docstring).

    * ``try_replay(kind, req_hash)`` — the earliest unused recorded answer to that request,
      or ``_MISS``. A miss changes nothing: no entry is dropped and the file is not rewritten.
    * ``append(kind, req_hash, result)`` — records a new answer. It is never handed back to
      the run that recorded it: only what was on file when replay began can be replayed.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._entries: list[JournalEntry] = []
        self._path = path
        # Recorded entries (indexes into ``_entries``) by request, oldest first, minus those
        # already replayed. Built by ``_replay_queues`` when replay first needs it.
        self._replay: dict[tuple[str, str], deque[int]] | None = None
        self._replayed = 0

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

    # -- replay / record -----------------------------------------------------

    def _replay_queues(self) -> dict[tuple[str, str], deque[int]]:
        """What can be replayed: the entries on file when this is first needed."""
        if self._replay is None:
            self._replay = {}
            for index, entry in enumerate(self._entries):
                self._replay.setdefault((entry.kind, entry.req_hash), deque()).append(index)
        return self._replay

    def try_replay(self, kind: str, req_hash: str) -> Any:
        """The earliest recorded answer to this request not yet replayed, else ``_MISS``."""
        queue = self._replay_queues().get((kind, req_hash))
        if not queue:
            return _MISS
        self._replayed += 1
        return self._entries[queue.popleft()].result

    def append(self, kind: str, req_hash: str, result: Any) -> None:
        """Record a new host-call result."""
        self._replay_queues()  # fix what may be replayed before this run adds to the journal
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

    # -- introspection -------------------------------------------------------

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def replayed_count(self) -> int:
        """How many calls were answered from the journal."""
        return self._replayed
