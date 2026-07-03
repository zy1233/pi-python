"""Shared repo helpers, ported from pi's session/repo-utils.ts."""

from __future__ import annotations

from typing import Any

from ..types import MessageEntry, SessionError, SessionStorage, SessionTreeEntry


async def get_entries_to_fork(
    storage: SessionStorage,
    options: dict[str, Any] | None = None,
) -> list[SessionTreeEntry]:
    """Resolve which entries a fork copies (pi `getEntriesToFork` semantics).

    - No ``entryId``: copy every entry (the whole tree, branches included).
    - ``position="at"``: copy the path from root to the target entry.
    - Default ``position="before"``: the target must be a user message; copy
      the path up to its parent (edit-and-resend scenario).
    """
    options = options or {}
    entry_id = options.get("entryId")
    if not entry_id:
        return await storage.get_entries()
    target = await storage.get_entry(entry_id)
    if target is None:
        raise SessionError("invalid_fork_target", f"Entry {entry_id} not found")
    if options.get("position", "before") == "at":
        effective_leaf_id: str | None = target.id
    else:
        if not (isinstance(target, MessageEntry) and target.message.get("role") == "user"):
            raise SessionError("invalid_fork_target", f"Entry {entry_id} is not a user message")
        effective_leaf_id = target.parentId
    return await storage.get_path_to_root(effective_leaf_id)
