"""Read-only git workspace snapshot for the system prompt.

Failures, timeouts, and non-repos omit the section. Nothing is written to the session.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


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


def format_git_status(snapshot: GitSnapshot) -> str:
    """Render the ``<git_status>`` block inserted into the system prompt."""
    parts = ["<git_status>", f"Branch: {snapshot.branch}"]
    if snapshot.status_porcelain:
        parts.append(snapshot.status_porcelain)
    parts.append("</git_status>")
    return "\n".join(parts)


def _git(cwd: str, args: list[str], timeout_seconds: float) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.replace("\r\n", "\n").strip()
