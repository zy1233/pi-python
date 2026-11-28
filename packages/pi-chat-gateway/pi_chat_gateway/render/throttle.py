"""At most one card update per interval, carrying everything that changed in between."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class Throttle:
    def __init__(
        self,
        interval: float,
        flush: Callable[[], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval
        self._flush = flush
        self._clock = clock
        self._last = float("-inf")
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def poke(self) -> None:
        """Something changed: flush now if the interval has passed, else once it has."""
        if self._task is not None and not self._task.done():
            return
        delay = max(0.0, self._last + self._interval - self._clock())
        self._task = asyncio.get_running_loop().create_task(self._later(delay))

    async def _later(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self._run()

    async def _run(self) -> None:
        async with self._lock:
            self._last = self._clock()
            try:
                await self._flush()
            except Exception:
                logger.warning("updating the card failed", exc_info=True)

    async def flush_now(self) -> None:
        """The final state goes out at once, after any update that is still in flight."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._run()

    async def close(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
