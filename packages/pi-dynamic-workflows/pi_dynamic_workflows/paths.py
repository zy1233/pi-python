"""Where a workflow script may send a sub-agent: inside the project, nowhere else."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_subagent_cwd(requested: str, root: str) -> str:
    """The absolute, symlink-free form of *requested*, provided it lies inside *root*.

    A relative *requested* is read against *root* (not against the process's working
    directory, which is whatever the host happened to be started in). Links are followed
    before the comparison, so a symlink or junction inside the project that points out of
    it does not let a path through. The comparison is by path component (``/p/project-x``
    is not inside ``/p/project``) and by the platform's own rules for case, separators and
    drives. The directory need not exist: where it *would* be is what counts.

    Raises ``TypeError`` for a *requested* that is not a string and ``ValueError`` for one
    that is outside *root* (another drive, a ``..`` that climbs out, a link that leads out)
    or cannot be a path at all.
    """
    if not isinstance(requested, str):
        raise TypeError(f"cwd must be a string, not {type(requested).__name__}")
    if "\x00" in requested:  # not every platform's realpath notices, and none can use it
        raise ValueError(f"Invalid cwd {requested!r}: embedded NUL character")
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(real_root, requested))
    if not Path(target).is_relative_to(real_root):
        raise ValueError(
            f"cwd '{requested}' is outside the project directory '{root}'; "
            "a workflow's sub-agents can only run inside it"
        )
    return target
