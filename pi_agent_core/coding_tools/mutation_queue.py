"""Per-file mutation mutex (port of pi ``withFileMutationQueue``).

The loop runs tools in parallel (audit D1), so concurrent write/edit calls
targeting the same file must serialize to avoid interleaved writes. Keyed by
``os.path.realpath`` so different spellings of the same file share one lock.
In-process only, mirroring pi (no cross-process guarantee).
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# Weak registry: idle locks are reclaimed once no caller holds them, which also
# prevents a lock created on one event loop from leaking into a later loop
# (each test run starts with a clean slate).
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock_for(absolute_path: str) -> asyncio.Lock:
    # No awaits between get and set, so get-or-create is atomic per event loop.
    key = os.path.realpath(absolute_path)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def with_file_mutation_queue(absolute_path: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run *fn* while holding the mutation lock for *absolute_path*."""
    async with _lock_for(absolute_path):
        return await fn()
