"""The state of one turn, built from the agent's ``session/update`` notifications."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

STATUSES = ("thinking", "running", "waiting", "done", "cancelled", "error")


@dataclass
class ToolLine:
    tool_call_id: str
    title: str
    kind: str
    status: str
    raw_input: Any
    started: float
    ended: float | None = None


def _text_of(content: Any) -> str:
    if getattr(content, "type", None) == "text":
        return getattr(content, "text", "") or ""
    return ""


class TurnView:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.status = "thinking"
        self.text_parts: list[str] = []
        self.thought_parts: list[str] = []
        self.tools: dict[str, ToolLine] = {}
        self.used: int | None = None
        self.size: int | None = None
        self.stop_reason: str | None = None
        self.error: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def thought(self) -> str:
        return "".join(self.thought_parts)

    @property
    def finished(self) -> bool:
        return self.status in ("done", "cancelled", "error")

    def apply(self, update: Any) -> bool:
        """Take one update in. ``True`` when something the card shows changed."""
        kind = getattr(update, "session_update", None)
        if kind == "agent_message_chunk":
            text = _text_of(update.content)
            if text:
                self.text_parts.append(text)
                if self.status == "thinking":
                    self.status = "running"
                return True
        elif kind == "agent_thought_chunk":
            text = _text_of(update.content)
            if text:
                self.thought_parts.append(text)
                return True
        elif kind == "tool_call":
            self.tools[update.tool_call_id] = ToolLine(
                tool_call_id=update.tool_call_id,
                title=update.title or update.kind or "tool",
                kind=update.kind or "other",
                status=update.status or "pending",
                raw_input=update.raw_input,
                started=self._clock(),
            )
            if self.status == "thinking":
                self.status = "running"
            return True
        elif kind == "tool_call_update":
            line = self.tools.get(update.tool_call_id)
            if line is None:
                line = ToolLine(
                    tool_call_id=update.tool_call_id,
                    title=update.title or "tool",
                    kind=update.kind or "other",
                    status=update.status or "pending",
                    raw_input=update.raw_input,
                    started=self._clock(),
                )
                self.tools[update.tool_call_id] = line
            if update.title:
                line.title = update.title
            if update.raw_input is not None:
                line.raw_input = update.raw_input
            if update.status:
                line.status = update.status
                if update.status in ("completed", "failed"):
                    line.ended = self._clock()
            return True
        elif kind == "usage_update":
            self.used = getattr(update, "used", None)
            self.size = getattr(update, "size", None)
            return True
        return False

    def raw_input_of(self, tool_call_id: str) -> Any:
        line = self.tools.get(tool_call_id)
        return line.raw_input if line else None

    def waiting(self) -> None:
        if not self.finished:
            self.status = "waiting"

    def resumed(self) -> None:
        if self.status == "waiting":
            self.status = "running"

    def finish(self, stop_reason: str | None) -> None:
        self.stop_reason = stop_reason
        self.status = "cancelled" if stop_reason == "cancelled" else "done"

    def fail(self, error: str) -> None:
        self.error = error
        self.status = "error"
