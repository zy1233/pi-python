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


def _text(output: bytes) -> str:
    """Git's stderr as text for a message. (Patches are never decoded: they are bytes.)"""
    return output.decode("utf-8", errors="replace").strip()


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
            raise RuntimeError(f"git worktree add failed: {_text(stderr)}")

        if config.copy_mode == "snapshot":
            await self._apply_snapshot(wt_dir)

        self._active.append(wt_dir)
        return wt_dir

    async def _apply_snapshot(self, wt_dir: str) -> None:
        """Copy staged + unstaged changes from source into the worktree.

        After applying dirty changes, creates a temporary baseline commit so
        that ``collect_diff()`` only captures the agent's delta — not the
        pre-existing dirty state that the source cwd already contains.
        """
        # ``--binary``: without it a changed binary file is a "Binary files differ" line that
        # ``git apply`` refuses, and that one file costs the worktree the whole snapshot.
        _, diff_unstaged, _ = await _run_git(["diff", "--binary", "HEAD"], cwd=self._source_cwd)
        if diff_unstaged.strip():
            rc, _, err = await _run_git(["apply", "--allow-empty"], cwd=wt_dir, stdin=diff_unstaged)
            if rc != 0:
                logger.warning("Snapshot unstaged apply failed: %s", _text(err))

        _, diff_staged, _ = await _run_git(
            ["diff", "--binary", "--cached", "HEAD"], cwd=self._source_cwd
        )
        if diff_staged.strip():
            rc, _, err = await _run_git(
                ["apply", "--cached", "--allow-empty"],
                cwd=wt_dir,
                stdin=diff_staged,
            )
            if rc != 0:
                logger.warning("Snapshot staged apply failed: %s", _text(err))

        has_changes = bool(diff_unstaged.strip() or diff_staged.strip())
        if has_changes:
            await _run_git(["add", "-A"], cwd=wt_dir)
            await _run_git(
                [
                    "-c",
                    "user.email=pi@local",
                    "-c",
                    "user.name=pi",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "pi-snapshot-baseline",
                ],
                cwd=wt_dir,
            )

    async def collect_diff(self, worktree_path: str) -> bytes:
        """The sub-agent's changes in the worktree, as a patch (bytes: never decoded).

        Everything git does not ignore is staged first, so files the agent *created* are in
        the patch; ``git diff HEAD`` alone lists tracked files only. ``--binary`` carries
        binary files, and bytes carry files that are not UTF-8: a decode/encode round trip
        damages them and ``git apply`` then refuses the patch. HEAD is the source's commit,
        or the snapshot baseline when the source was dirty, so a dirty source's own changes
        are not part of the patch.
        """
        rc, _, err = await _run_git(["add", "-A"], cwd=worktree_path)
        if rc != 0:
            raise RuntimeError(f"git add failed: {_text(err)}")
        rc, stdout, err = await _run_git(
            ["diff", "--binary", "--cached", "HEAD"], cwd=worktree_path
        )
        if rc != 0:
            raise RuntimeError(f"git diff failed: {_text(err)}")
        return stdout

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
        rc, _, err = await _run_git(["apply", "--allow-empty"], cwd=dest, stdin=diff)
        if rc != 0:
            raise RuntimeError(f"apply_changes failed: {_text(err)}")

    async def cleanup(self, worktree_path: str) -> None:
        """Remove a linked worktree."""
        rc, _, err = await _run_git(
            ["worktree", "remove", "--force", worktree_path],
            cwd=self._source_cwd,
        )
        if rc != 0:
            logger.warning("worktree remove failed: %s", _text(err))
        if worktree_path in self._active:
            self._active.remove(worktree_path)

    async def cleanup_all(self) -> None:
        """Remove all worktrees created by this manager."""
        for wt in list(self._active):
            await self.cleanup(wt)
