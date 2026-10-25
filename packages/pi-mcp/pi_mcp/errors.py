"""What went wrong, in words the user can read, and whether a server is gone.

The MCP SDK runs a task group per transport, so the failure of a connection arrives as an
``ExceptionGroup``. ``first_leaf`` takes the first real error out of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Literal

import anyio
import httpx
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from pi_mcp.redact import one_line, redact
from pi_mcp.settings import MAX_RESULT_BYTES, REASON_LIMIT

Lost = Literal["unsent", "lost"]


class StartupTimeout(Exception):
    """A server did not finish its start-up (``initialize`` and the tool list) in time."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"it did not answer within {seconds:g} s")
        self.seconds = seconds


class ConnectionUnavailable(RuntimeError):
    """A tool call cannot go to its server. The message is for the model, so it is a sentence."""


class CallFailed(RuntimeError):
    """A tool call ended without an answer of the server: aborted, or out of time."""


class ConnectionEnded(Exception):
    """The connection of a server ended while a call waited for its answer.

    A transport that fails (an HTTP error in the middle of a call, say) is cancelled, and the
    MCP SDK does not tell the requests that are still open. ``failure`` is what ended the
    connection, when it is known.
    """

    def __init__(self, failure: BaseException | None = None) -> None:
        super().__init__("the connection ended")
        self.failure = failure


def leaves(exc: BaseException) -> list[BaseException]:
    """The errors inside *exc*: itself, or the leaves of an ``ExceptionGroup`` (nested too)."""
    if isinstance(exc, BaseExceptionGroup):
        found: list[BaseException] = []
        for inner in exc.exceptions:
            found.extend(leaves(inner))
        return found
    return [exc]


def first_leaf(exc: BaseException) -> BaseException:
    """The first error inside *exc* that is not a cancellation (or the first one, if all are)."""
    found = leaves(exc)
    for leaf in found:
        if not isinstance(leaf, asyncio.CancelledError):
            return leaf
    return found[0]


def is_command_not_found(exc: BaseException) -> bool:
    return isinstance(first_leaf(exc), FileNotFoundError)


def _message(leaf: BaseException) -> str:
    return leaf.error.message if isinstance(leaf, McpError) else str(leaf)


def describe_error(exc: BaseException, secrets: Iterable[str], *, limit: int = REASON_LIMIT) -> str:
    """One line about *exc* for a user: the first real error, cleaned of secrets, cut at *limit*.

    The cleaning comes first. A secret that is cut in two by the limit could not be found any
    more, and half of a token would be shown.
    """
    leaf = first_leaf(exc)
    text = one_line(redact(_message(leaf), secrets))
    if not text:
        text = type(leaf).__name__
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def error_text_for_model(exc: BaseException, secrets: Iterable[str]) -> str:
    """The text of an error that goes to the model: all lines are kept (a server may send a
    traceback or JSON), secrets are masked, and a very long text is cut."""
    leaf = first_leaf(exc)
    text = redact(_message(leaf), secrets).strip() or type(leaf).__name__
    data = text.encode("utf-8")
    if len(data) > MAX_RESULT_BYTES:
        text = data[:MAX_RESULT_BYTES].decode("utf-8", errors="ignore") + "\n[Error text cut.]"
    return text


def connection_lost(exc: BaseException) -> Lost | None:
    """Say whether *exc* shows that the server is gone.

    ``"unsent"``: the request never left (the stream was closed before it, no connection could
    be made, or the server answered that the session is over), so it is safe to send it again
    after a reconnect. ``"lost"``: the connection ended while the request was in flight; the
    server may have done the work, so the call is not repeated. ``None``: an error of another
    kind.
    """
    for leaf in leaves(exc):
        if isinstance(
            leaf,
            anyio.ClosedResourceError
            | anyio.BrokenResourceError
            | httpx.ConnectError  # no connection was made: nothing was sent
            | httpx.ConnectTimeout,
        ):
            return "unsent"
        if isinstance(leaf, McpError):
            if leaf.error.code == CONNECTION_CLOSED:
                return "lost"
            if "session terminated" in leaf.error.message.lower():
                return "unsent"
        if isinstance(
            leaf,
            anyio.EndOfStream
            | ConnectionError
            | httpx.RemoteProtocolError
            | httpx.ReadError
            | httpx.WriteError
            | httpx.CloseError,
        ):
            return "lost"
    return None
