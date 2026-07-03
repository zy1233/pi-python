"""Shared truncation utilities for tool outputs (port of pi ``truncate.ts``).

Truncation is based on two independent limits — whichever is hit first wins:
a line limit (default 2000 lines) and a byte limit (default 50KB, counted as
UTF-8). Head truncation never returns partial lines; tail truncation may keep
a partial last line (bash tail edge case). Cut points always fall on UTF-8
character boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB
GREP_MAX_LINE_LENGTH = 500  # max chars per grep match line

TruncatedBy = Literal["lines", "bytes"]


@dataclass
class TruncationResult:
    """Outcome of a head/tail truncation (mirrors pi's ``TruncationResult``)."""

    content: str
    truncated: bool
    truncated_by: TruncatedBy | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize with pi's camelCase keys (tool-result ``details`` shape)."""
        return {
            "content": self.content,
            "truncated": self.truncated,
            "truncatedBy": self.truncated_by,
            "totalLines": self.total_lines,
            "totalBytes": self.total_bytes,
            "outputLines": self.output_lines,
            "outputBytes": self.output_bytes,
            "lastLinePartial": self.last_line_partial,
            "firstLineExceedsLimit": self.first_line_exceeds_limit,
            "maxLines": self.max_lines,
            "maxBytes": self.max_bytes,
        }


def format_size(n_bytes: int) -> str:
    """Human-readable size: ``512B`` / ``50.0KB`` / ``1.2MB``."""
    if n_bytes < 1024:
        return f"{n_bytes}B"
    if n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.1f}KB"
    return f"{n_bytes / (1024 * 1024):.1f}MB"


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    """Cap a single line at *max_chars*, appending a ``[truncated]`` suffix."""
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_lines_for_counting(content: str) -> list[str]:
    # A trailing newline does not start a new (empty) line, matching pi.
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _untruncated(
    content: str, total_lines: int, total_bytes: int, max_lines: int, max_bytes: int
) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=False,
        truncated_by=None,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=total_lines,
        output_bytes=total_bytes,
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the first N lines/bytes (file reads: the beginning matters).

    Never returns partial lines. When the first line alone exceeds the byte
    limit the content is empty and ``first_line_exceeds_limit`` is set.
    """
    total_bytes = _byte_len(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _untruncated(content, total_lines, total_bytes, max_lines, max_bytes)

    if _byte_len(lines[0]) > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=0,
            output_bytes=0,
            last_line_partial=False,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    out: list[str] = []
    out_bytes = 0
    truncated_by: TruncatedBy = "lines"
    for i, line in enumerate(lines[:max_lines]):
        line_bytes = _byte_len(line) + (1 if i > 0 else 0)  # +1 for the joining newline
        if out_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        out.append(line)
        out_bytes += line_bytes

    out_content = "\n".join(out)
    return TruncationResult(
        content=out_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(out),
        output_bytes=_byte_len(out_content),
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the last N lines/bytes (bash output: errors sit at the end).

    May return a partial first line when the very last line of the original
    content alone exceeds the byte limit (``last_line_partial``).
    """
    total_bytes = _byte_len(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _untruncated(content, total_lines, total_bytes, max_lines, max_bytes)

    out: list[str] = []  # collected end-first, reversed at the end
    out_bytes = 0
    truncated_by: TruncatedBy = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(out) >= max_lines:
            break
        line_bytes = _byte_len(line) + (1 if out else 0)  # +1 for the joining newline
        if out_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not out:
                # The last line alone exceeds the limit: keep its tail (partial).
                partial = _truncate_str_to_bytes_from_end(line, max_bytes)
                out.append(partial)
                out_bytes = _byte_len(partial)
                last_line_partial = True
            break
        out.append(line)
        out_bytes += line_bytes
    out.reverse()

    out_content = "\n".join(out)
    return TruncationResult(
        content=out_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(out),
        output_bytes=_byte_len(out_content),
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _truncate_str_to_bytes_from_end(text: str, max_bytes: int) -> str:
    """Take the trailing *max_bytes* of *text*, landing on a UTF-8 boundary."""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    start = len(data) - max_bytes
    # Skip UTF-8 continuation bytes (0b10xxxxxx) to reach a character start.
    while start < len(data) and (data[start] & 0xC0) == 0x80:
        start += 1
    return data[start:].decode("utf-8")
