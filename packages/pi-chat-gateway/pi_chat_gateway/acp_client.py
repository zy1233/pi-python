"""The ACP client side: what the agents ask of the gateway, routed to the session's runner.

The agent may speak about a session right after ``session/new`` answers (the trust question,
``available_commands_update``), before the gateway has filed the new id. Such messages wait a
moment for the runner instead of being dropped.

The gateway offers no ``fs/*`` or ``terminal/*`` methods: the agent uses none of them (its tools
run on this machine), and it does not ask the client for them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from acp import RequestError
from acp.schema import DeniedOutcome, RequestPermissionResponse

logger = logging.getLogger(__name__)

REGISTRATION_WAIT = 5.0


class SessionHandler(Protocol):
    async def on_update(self, update: Any) -> None: ...

    async def on_permission(
        self, tool_call: Any, options: list[Any]
    ) -> RequestPermissionResponse: ...

    async def on_queue_update(self, params: dict[str, Any]) -> None: ...

    async def on_btw_update(self, params: dict[str, Any]) -> None: ...


class SessionRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, SessionHandler] = {}
        self._waiters: dict[str, asyncio.Event] = {}

    def register(self, session_id: str, handler: SessionHandler) -> None:
        self._handlers[session_id] = handler
        event = self._waiters.pop(session_id, None)
        if event is not None:
            event.set()

    def forget(self, session_id: str) -> None:
        self._handlers.pop(session_id, None)

    async def handler(self, session_id: str) -> SessionHandler | None:
        handler = self._handlers.get(session_id)
        if handler is not None:
            return handler
        event = self._waiters.setdefault(session_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), REGISTRATION_WAIT)
        except TimeoutError:
            self._waiters.pop(session_id, None)
            return None
        return self._handlers.get(session_id)


class GatewayClient:
    def __init__(self, router: SessionRouter) -> None:
        self._router = router

    def on_connect(self, conn: Any) -> None:  # the SDK hands the connection over; not needed
        return None

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any
    ) -> RequestPermissionResponse:
        handler = await self._router.handler(session_id)
        if handler is None:
            logger.warning(
                "a permission request for an unknown session %s was cancelled", session_id
            )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return await handler.on_permission(tool_call, list(options))

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        handler = await self._router.handler(session_id)
        if handler is not None:
            await handler.on_update(update)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        session_id = params.get("sessionId") if isinstance(params, dict) else None
        if not isinstance(session_id, str):
            return
        handler = await self._router.handler(session_id)
        if handler is None:
            return
        if method == "pi/queue_update":
            await handler.on_queue_update(params)
        elif method == "pi/btw_update":
            await handler.on_btw_update(params)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def read_text_file(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("fs/write_text_file")

    async def create_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/kill")
