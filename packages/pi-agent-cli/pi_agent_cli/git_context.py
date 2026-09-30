"""Read-only git workspace snapshot for the system prompt.

Failures, timeouts, and non-repos omit the section. Nothing is written to the session.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitSnapshot:
    branch: str
    """Branch name, or ``HEAD (detached at <short-sha>)``."""

    status_porcelain: str
    """Truncated ``git status -b`` text, including the ``##`` line."""

    truncated: bool


def snapshot_git_context(
    cwd: str,
    *,
    timeout_seconds: float,
    max_lines: int,
) -> GitSnapshot | None:
    """Return a bounded status snapshot, or None when git cannot answer."""
    inside = _git(cwd, ["rev-parse", "--is-inside-work-tree"], timeout_seconds)
    if inside != "true":
        return None

    abbrev = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"], timeout_seconds)
    if abbrev is None:
        return None
    if abbrev == "HEAD":
        short = _git(cwd, ["rev-parse", "--short", "HEAD"], timeout_seconds)
        if not short:
            return None
        branch = f"HEAD (detached at {short})"
    else:
        branch = abbrev

    status = _git(
        cwd,
        ["--no-optional-locks", "status", "--porcelain=v1", "-b", "--untracked-files=normal"],
        timeout_seconds,
    )
    if status is None:
        return None

    lines = status.splitlines()
    limit = max(max_lines, 0)
    truncated = len(lines) > limit
    if truncated:
        lines = lines[:limit]
        lines.append("# … truncated")
    return GitSnapshot(branch=branch, status_porcelain="\n".join(lines), truncated=truncated)


SNAPSHOT_NOTICE = (
    "Snapshot taken at the start of this session; it is not refreshed as files change. "
    "Run `git status` for the current state."
)


def format_git_status(snapshot: GitSnapshot) -> str:
    """Render the ``<git_status>`` block inserted into the system prompt.

    The harness caches the system prompt for the whole session (stable prefix for the
    provider's prompt cache), so this block cannot track the working tree. The notice
    says so, rather than letting the model take stale state for the current one.
    """
    parts = ["<git_status>", SNAPSHOT_NOTICE, f"Branch: {snapshot.branch}"]
    if snapshot.status_porcelain:
        parts.append(snapshot.status_porcelain)
    parts.append("</git_status>")
    return "\n".join(parts)


def _git(cwd: str, args: list[str], timeout_seconds: float) -> str | None:
    """Run one read-only git command; ``None`` on any failure.

    The result feeds an optional prompt section, so nothing that goes wrong here
    (missing git, timeout, undecodable output, ...) may escape and fail the turn.
    """
    try:
        completed = subprocess.run(
            # quotePath=false: emit non-ASCII paths as UTF-8 rather than "\344\270\255" escapes.
            ["git", "-C", cwd, "-c", "core.quotePath=false", *args],
            check=False,
            capture_output=True,
            # `text=True` alone decodes with the *locale* codec (cp936 on Chinese Windows),
            # but git writes UTF-8: that raised on some branch names and turned the rest
            # into mojibake. Pin the codec; `replace` makes decoding infallible.
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("git %s failed; omitting git context", args, exc_info=True)
        return None
    if completed.returncode != 0 or completed.stdout is None:
        return None
    return completed.stdout.replace("\r\n", "\n").strip()
