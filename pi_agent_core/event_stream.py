"""Async event stream with result() — mirrors pi-ai EventStream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Generic, TypeVar

from pi_agent_core.messages import AssistantMessage
from pi_agent_core.types import AssistantMessageEvent

T = TypeVar("T")


class EventStream(Generic[T]):
    def __init__(
        self,
        is_terminal: Callable[[T], bool],
        get_result: Callable[[T], list],
    ):
        self._is_terminal = is_terminal
        self._get_result = get_result
        self._queue: asyncio.Queue[T | None] = asyncio.Queue()
        self._result: list | None = None
        self._done = False

    def push(self, event: T) -> None:
        if self._done:
            return
        self._queue.put_nowait(event)
        if self._is_terminal(event):
            self._result = self._get_result(event)
            self._done = True
            self._queue.put_nowait(None)

    def end(self, result: list | None = None) -> None:
        if self._done:
            return
        self._result = result
        self._done = True
        self._queue.put_nowait(None)

    def __aiter__(self) -> AsyncIterator[T]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[T]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    async def result(self) -> list:
        async for _ in self:
            pass
        return self._result or []


class AssistantMessageEventStream(EventStream[AssistantMessageEvent]):
    def __init__(self) -> None:
        super().__init__(
            is_terminal=lambda e: e.type in ("done", "error"),
            get_result=lambda e: [],  # unused; we store AssistantMessage separately
        )
        self._final_message: AssistantMessage | None = None

    def set_final_message(self, message: AssistantMessage) -> None:
        self._final_message = message

    async def message_result(self) -> AssistantMessage:
        if self._final_message is not None:
            return self._final_message
        async for _ in self:
            pass
        if self._final_message is None:
            raise RuntimeError("Stream ended without final assistant message")
        return self._final_message
