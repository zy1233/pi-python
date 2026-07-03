"""Streaming output accumulator (port of pi ``output-accumulator.ts``).

Tracks a subprocess's merged output with bounded memory: only a rolling
decoded tail (~4x the byte limit) is kept for display snapshots, while exact
line/byte totals are counted incrementally. When the output exceeds the
limits, the full raw bytes spill into a temp file so nothing is lost.
"""

from __future__ import annotations

import codecs
import os
import secrets
import tempfile
from dataclasses import dataclass, replace
from typing import BinaryIO

from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    truncate_tail,
)


@dataclass
class OutputSnapshot:
    content: str
    truncation: TruncationResult
    full_output_path: str | None


class OutputAccumulator:
    """Incrementally accumulates streamed output chunks (tail-biased)."""

    def __init__(
        self,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        temp_file_prefix: str = "pi-output",
    ) -> None:
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self._max_rolling_bytes = max(max_bytes * 2, 1)
        self._temp_file_prefix = temp_file_prefix
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        self._raw_chunks: list[bytes] = []
        self._tail_text = ""
        self._tail_bytes = 0
        self._tail_starts_at_line_boundary = True
        self._total_raw_bytes = 0
        self._total_decoded_bytes = 0
        self._completed_lines = 0
        self._total_lines = 0
        self._current_line_bytes = 0
        self._has_open_line = False
        self._finished = False

        self._temp_file_path: str | None = None
        self._temp_file: BinaryIO | None = None

    def append(self, data: bytes) -> None:
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")
        self._total_raw_bytes += len(data)
        self._append_decoded_text(self._decoder.decode(data))

        if self._temp_file is not None or self._should_use_temp_file():
            self._ensure_temp_file()
            assert self._temp_file is not None
            self._temp_file.write(data)
        elif data:
            self._raw_chunks.append(data)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._append_decoded_text(self._decoder.decode(b"", final=True))
        if self._should_use_temp_file():
            self._ensure_temp_file()

    def snapshot(self, *, persist_if_truncated: bool = False) -> OutputSnapshot:
        tail = truncate_tail(
            self._snapshot_text(), max_lines=self._max_lines, max_bytes=self._max_bytes
        )
        # The rolling tail may already have discarded earlier output, so the
        # global counters — not the local tail truncation — decide the flags.
        truncated = (
            self._total_lines > self._max_lines or self._total_decoded_bytes > self._max_bytes
        )
        truncated_by = None
        if truncated:
            truncated_by = tail.truncated_by or (
                "bytes" if self._total_decoded_bytes > self._max_bytes else "lines"
            )
        truncation = replace(
            tail,
            truncated=truncated,
            truncated_by=truncated_by,
            total_lines=self._total_lines,
            total_bytes=self._total_decoded_bytes,
            max_lines=self._max_lines,
            max_bytes=self._max_bytes,
        )
        if persist_if_truncated and truncated:
            self._ensure_temp_file()
        return OutputSnapshot(
            content=truncation.content,
            truncation=truncation,
            full_output_path=self._temp_file_path,
        )

    def close_temp_file(self) -> None:
        if self._temp_file is None:
            return
        f = self._temp_file
        self._temp_file = None
        f.close()

    def get_last_line_bytes(self) -> int:
        return self._current_line_bytes

    # --- internals ---

    def _append_decoded_text(self, text: str) -> None:
        if not text:
            return
        n_bytes = len(text.encode("utf-8"))
        self._total_decoded_bytes += n_bytes
        self._tail_text += text
        self._tail_bytes += n_bytes
        if self._tail_bytes > self._max_rolling_bytes * 2:
            self._trim_tail()

        newline_count = text.count("\n")
        if newline_count == 0:
            self._current_line_bytes += n_bytes
            self._has_open_line = True
        else:
            self._completed_lines += newline_count
            after_last_newline = text[text.rfind("\n") + 1 :]
            self._current_line_bytes = len(after_last_newline.encode("utf-8"))
            self._has_open_line = bool(after_last_newline)
        self._total_lines = self._completed_lines + (1 if self._has_open_line else 0)

    def _trim_tail(self) -> None:
        buffer = self._tail_text.encode("utf-8")
        if len(buffer) <= self._max_rolling_bytes:
            self._tail_bytes = len(buffer)
            return
        start = len(buffer) - self._max_rolling_bytes
        # Land on a UTF-8 character boundary (skip continuation bytes).
        while start < len(buffer) and (buffer[start] & 0xC0) == 0x80:
            start += 1
        if start != 0:
            self._tail_starts_at_line_boundary = buffer[start - 1] == 0x0A
        self._tail_text = buffer[start:].decode("utf-8")
        self._tail_bytes = len(self._tail_text.encode("utf-8"))

    def _snapshot_text(self) -> str:
        if self._tail_starts_at_line_boundary:
            return self._tail_text
        # Drop the leading partial line so snapshots never start mid-line.
        first_newline = self._tail_text.find("\n")
        return self._tail_text if first_newline == -1 else self._tail_text[first_newline + 1 :]

    def _should_use_temp_file(self) -> bool:
        return (
            self._total_raw_bytes > self._max_bytes
            or self._total_decoded_bytes > self._max_bytes
            or self._total_lines > self._max_lines
        )

    def _ensure_temp_file(self) -> None:
        if self._temp_file_path is not None:
            return
        self._temp_file_path = os.path.join(
            tempfile.gettempdir(), f"{self._temp_file_prefix}-{secrets.token_hex(8)}.log"
        )
        self._temp_file = open(self._temp_file_path, "wb")  # noqa: SIM115 - long-lived handle
        for chunk in self._raw_chunks:
            self._temp_file.write(chunk)
        self._raw_chunks = []
