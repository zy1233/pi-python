"""Token budget tracking for workflow runs."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class TokenBudget:
    """Tracks token usage against an optional budget."""

    total: int | None = None
    _spent: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def spent(self) -> int:
        with self._lock:
            return self._spent

    def remaining(self) -> int | None:
        if self.total is None:
            return None
        with self._lock:
            return max(0, self.total - self._spent)

    def add(self, tokens: int) -> None:
        with self._lock:
            self._spent += tokens

    def exceeded(self) -> bool:
        if self.total is None:
            return False
        with self._lock:
            return self._spent >= self.total

    def to_dict(self) -> dict[str, int | None]:
        return {
            "total": self.total,
            "spent": self.spent(),
            "remaining": self.remaining(),
        }
