"""Git worktree isolation for concurrent subagent execution.

Creates linked worktrees via ``git worktree add --detach`` so each subagent
operates on an independent working directory that shares the ``.git`` store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class WorktreeConfig:
    """Configuration for git worktree creation."""

    copy_mode: Literal["snapshot", "clean"] = "snapshot"
    git_ref: str | None = None
    cleanup: bool = True


async def _run_git(
    args: list[str], cwd: str, stdin: bytes | None = None
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=stdin)
    return proc.returncode or 0, stdout or b"", stderr or b""


class WorktreeManager:
    """Create and manage git linked worktrees for isolated execution."""

    def __init__(self, source_cwd: str) -> None:
        self._source_cwd = source_cwd
        self._active: list[str] = []

    async def create(
        self,
        session_id: str | None = None,
        config: WorktreeConfig | None = None,
    ) -> str:
        """Create a new linked worktree and return its absolute path."""
        config = config or WorktreeConfig()
        tag = session_id or uuid.uuid4().hex[:12]
        wt_dir = os.path.join(tempfile.gettempdir(), f"pi-wt-{tag}")

        ref = config.git_ref or "HEAD"
        rc, _, stderr = await _run_git(
            ["worktree", "add", "--detach", wt_dir, ref],
            cwd=self._source_cwd,
        )
        if rc != 0:
            raise RuntimeError(f"git worktree add failed: {stderr.decode().strip()}")

        if config.copy_mode == "snapshot":
            await self._apply_snapshot(wt_dir)

        self._active.append(wt_dir)
        return wt_dir

    async def _apply_snapshot(self, wt_dir: str) -> None:
        """Copy staged + unstaged changes from source into the worktree."""
        _, diff_unstaged, _ = await _run_git(["diff", "HEAD"], cwd=self._source_cwd)
        if diff_unstaged.strip():
            rc, _, err = await _run_git(["apply", "--allow-empty"], cwd=wt_dir, stdin=diff_unstaged)
            if rc != 0:
                logger.warning("Snapshot unstaged apply failed: %s", err.decode().strip())

        _, diff_staged, _ = await _run_git(["diff", "--cached", "HEAD"], cwd=self._source_cwd)
        if diff_staged.strip():
            rc, _, err = await _run_git(
                ["apply", "--cached", "--allow-empty"],
                cwd=wt_dir,
                stdin=diff_staged,
            )
            if rc != 0:
                logger.warning("Snapshot staged apply failed: %s", err.decode().strip())

    async def collect_diff(self, worktree_path: str) -> str:
        """Return ``git diff HEAD`` from the worktree."""
        _, stdout, _ = await _run_git(["diff", "HEAD"], cwd=worktree_path)
        return stdout.decode(errors="replace")

    async def apply_changes(
        self,
        worktree_path: str,
        *,
        target: str | None = None,
    ) -> None:
        """Apply worktree changes back to *target* (default: source cwd)."""
        diff = await self.collect_diff(worktree_path)
        if not diff.strip():
            return
        dest = target or self._source_cwd
        rc, _, err = await _run_git(["apply", "--allow-empty"], cwd=dest, stdin=diff.encode())
        if rc != 0:
            raise RuntimeError(f"apply_changes failed: {err.decode().strip()}")

    async def cleanup(self, worktree_path: str) -> None:
        """Remove a linked worktree."""
        rc, _, err = await _run_git(
            ["worktree", "remove", "--force", worktree_path],
            cwd=self._source_cwd,
        )
        if rc != 0:
            logger.warning("worktree remove failed: %s", err.decode().strip())
        if worktree_path in self._active:
            self._active.remove(worktree_path)

    async def cleanup_all(self) -> None:
        """Remove all worktrees created by this manager."""
        for wt in list(self._active):
            await self.cleanup(wt)
