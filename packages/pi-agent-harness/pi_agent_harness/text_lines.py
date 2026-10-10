"""Read the first lines of a text file without reading the rest of it."""

from __future__ import annotations

from pathlib import Path


def read_head_lines(path: str | Path, max_lines: int | None = None) -> list[str]:
    """``Path(path).read_text(encoding="utf-8").splitlines()[:max_lines]``, minus the tail.

    Same lines, same newline handling (universal newlines, then ``str.splitlines``), but the
    file is read only as far as ``max_lines`` needs. A session list wants one line from each
    file, the header, and the files can be megabytes.
    """
    with open(path, encoding="utf-8") as handle:
        if max_lines is None or max_lines < 0:
            everything = handle.read().splitlines()
            return everything if max_lines is None else everything[:max_lines]
        lines: list[str] = []
        while len(lines) < max_lines:
            segment = handle.readline()
            if not segment:
                break
            # A segment ends at a "\n" (after translation), which is also where splitlines splits,
            # so splitting segment by segment gives what splitting the whole text would; the
            # rest of its separators (U+2028 and friends) are handled by splitlines itself.
            lines.extend(segment.splitlines())
        return lines[:max_lines]
