"""Path resolution, glob translation, and image sniffing for coding tools."""

from __future__ import annotations

import os
import re
import sys

# Directories pruned by the pure-Python walkers (find, grep fallback). This is
# the declared divergence from pi: a fixed ignore list instead of .gitignore.
DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
    }
)


def wsl_mnt_path_to_windows(path: str) -> str | None:
    """Map ``/mnt/<drive>/rest`` → ``<drive>:/rest`` (WSL bind mounts)."""
    normalized = path.replace("\\", "/")
    prefix = "/mnt/"
    if not normalized.startswith(prefix) or len(normalized) <= len(prefix) + 2:
        return None
    body = normalized[len(prefix) :]
    if body[1] != "/":
        return None
    drive = body[0].upper()
    tail = body[2:]
    return f"{drive}:/{tail}" if tail else f"{drive}:/"


def normalize_host_path(path: str) -> str:
    """On Windows, translate WSL ``/mnt/<drive>/...`` paths to native drive paths.

    Without this, Windows resolves ``/mnt/d/work/foo`` as ``D:\\mnt\\d\\work\\foo``
    (drive-relative POSIX path), not ``D:\\work\\foo``.
    """
    converted = wsl_mnt_path_to_windows(path)
    if converted is None or sys.platform != "win32":
        return path
    return os.path.normpath(converted)


def resolve_to_cwd(path: str, cwd: str) -> str:
    """Resolve *path* against the tool's bound *cwd*.

    Absolute paths pass through; ``~`` expands to the user home; relative
    paths are joined onto *cwd*. The result is OS-normalized.
    """
    cwd = normalize_host_path(cwd)
    expanded = normalize_host_path(os.path.expanduser(path))
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(cwd, expanded))


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a regex over POSIX-style (``/``-separated) paths.

    Mirrors the fd glob subset pi's find relies on: ``**/`` matches any number
    of leading directories (including none), ``**`` matches anything, ``*``
    matches within a path segment, ``?`` matches a single non-separator char.
    Everything else is literal. Match with ``.fullmatch()``.
    """
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**/", i):
                parts.append("(?:.*/)?")
                i += 3
            elif pattern.startswith("**", i):
                parts.append(".*")
                i += 2
            else:
                parts.append("[^/]*")
                i += 1
        elif ch == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(ch))
            i += 1
    return re.compile("".join(parts))


def compile_glob(pattern: str) -> tuple[re.Pattern[str], bool]:
    """Compile a find/grep glob following pi's fd invocation semantics.

    Returns ``(regex, matches_path)``. A pattern without ``/`` matches the
    basename only; a pattern with ``/`` matches the relative POSIX path and is
    auto-prefixed with ``**/`` so it hits at any depth — unless it is anchored
    (leading ``/``, ``**/`` prefix, or exactly ``**``). A leading ``/`` anchors
    at the search root (pi's fd anchors at the filesystem root; the search
    root is the pure-Python equivalent). Match with ``regex.fullmatch``.
    """
    if "/" not in pattern:
        return glob_to_regex(pattern), False
    effective = pattern
    if effective.startswith("/"):
        effective = effective[1:]
    elif not effective.startswith("**/") and effective != "**":
        effective = "**/" + effective
    return glob_to_regex(effective), True


def detect_image_mime(buffer: bytes) -> str | None:
    """Sniff png/jpeg/gif/webp/bmp magic numbers.

    Hand-rolled because stdlib ``imghdr`` was removed in Python 3.13.
    Returns the MIME type, or None for non-image (or unsupported) content.
    """
    if buffer.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if buffer.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if buffer.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(buffer) >= 12 and buffer[:4] == b"RIFF" and buffer[8:12] == b"WEBP":
        return "image/webp"
    if buffer.startswith(b"BM"):
        return "image/bmp"
    return None
