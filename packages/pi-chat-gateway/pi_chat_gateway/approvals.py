"""Tool-call approvals that wait for a tap in the chat.

Each pending approval has a request id and a random nonce; both go into the buttons. A tap
counts only if the request is still open, the nonce matches, the option was offered, and the
person may approve (``Authorizer.may_approve``). The first tap that counts wins; later taps are
told the request is closed. An approval nobody answers in time is rejected.

Only ``allow_once`` and the reject kinds become buttons. ``allow_always`` is never offered from a
chat: the agent's tool calls do not use it, and the one request that does is the trust question,
which the gateway answers itself (``trust.py``).
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption, RequestPermissionResponse

BUTTON_KINDS = ("allow_once", "reject_once", "reject_always")


class Resolution(Enum):
    ALLOWED = "allowed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TapResult(Enum):
    OK = "ok"
    UNKNOWN = "unknown"  # no such request, or it is closed
    STALE = "stale"  # the nonce does not match
    FORBIDDEN = "forbidden"
    BAD_OPTION = "bad_option"


@dataclass
class Pending:
    request_id: str
    nonce: str
    session_key: object
    chat_id: str  # the chat of the session, which decides who may approve
    title: str
    options: list[PermissionOption]
    created_at: float
    future: asyncio.Future[RequestPermissionResponse]
    resolution: Resolution | None = None
    actor: str = ""
    card_targets: list[tuple[str, str]] = field(default_factory=list)  # (platform, message id)
    detail: str = ""  # the request as Markdown, to draw the card again once it is closed


def button_options(options: list[PermissionOption]) -> list[PermissionOption]:
    return [option for option in options if option.kind in BUTTON_KINDS]


def _reject_option(options: list[PermissionOption]) -> PermissionOption | None:
    return next((o for o in options if o.kind == "reject_once"), None) or next(
        (o for o in options if o.kind.startswith("reject")), None
    )


class ApprovalBroker:
    def __init__(
        self,
        *,
        timeout: float,
        on_closed: Callable[[Pending], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout
        self._on_closed = on_closed
        self._clock = clock
        self._pending: dict[str, Pending] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def open(
        self, *, session_key: object, chat_id: str, title: str, options: list[PermissionOption]
    ) -> Pending:
        loop = asyncio.get_running_loop()
        request_id = secrets.token_hex(8)
        pending = Pending(
            request_id=request_id,
            nonce=secrets.token_urlsafe(12),
            session_key=session_key,
            chat_id=chat_id,
            title=title,
            options=button_options(options),
            created_at=self._clock(),
            future=loop.create_future(),
        )
        self._pending[request_id] = pending
        self._timers[request_id] = loop.call_later(self._timeout, self._expire, request_id)
        return pending

    def get(self, request_id: str) -> Pending | None:
        return self._pending.get(request_id)

    def open_for(self, session_key: object) -> list[Pending]:
        return [p for p in self._pending.values() if p.session_key == session_key]

    def tap(
        self,
        *,
        request_id: str,
        nonce: str,
        option_id: str,
        actor: str,
        may_approve: Callable[[Pending], bool],
    ) -> tuple[TapResult, Pending | None]:
        pending = self._pending.get(request_id)
        if pending is None:
            return TapResult.UNKNOWN, None
        if not secrets.compare_digest(pending.nonce, nonce):
            return TapResult.STALE, pending
        if not may_approve(pending):
            return TapResult.FORBIDDEN, pending
        option = next((o for o in pending.options if o.option_id == option_id), None)
        if option is None:
            return TapResult.BAD_OPTION, pending
        resolution = Resolution.ALLOWED if option.kind == "allow_once" else Resolution.REJECTED
        self._close(pending, resolution, actor, _selected(option.option_id))
        return TapResult.OK, pending

    def cancel(self, request_id: str) -> None:
        pending = self._pending.get(request_id)
        if pending is not None:
            self._close(
                pending,
                Resolution.CANCELLED,
                "",
                RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")),
            )

    def cancel_session(self, session_key: object) -> None:
        for pending in self.open_for(session_key):
            self.cancel(pending.request_id)

    def _expire(self, request_id: str) -> None:
        pending = self._pending.get(request_id)
        if pending is None:
            return
        reject = _reject_option(pending.options)
        response = (
            _selected(reject.option_id)
            if reject is not None
            else RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        )
        self._close(pending, Resolution.TIMEOUT, "", response)

    def _close(
        self,
        pending: Pending,
        resolution: Resolution,
        actor: str,
        response: RequestPermissionResponse,
    ) -> None:
        self._pending.pop(pending.request_id, None)
        timer = self._timers.pop(pending.request_id, None)
        if timer is not None:
            timer.cancel()
        pending.resolution = resolution
        pending.actor = actor
        if not pending.future.done():
            pending.future.set_result(response)
        if self._on_closed is not None:
            self._on_closed(pending)


def _selected(option_id: str) -> RequestPermissionResponse:
    return RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id=option_id)
    )
