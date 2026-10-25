"""One MCP server of one session: its task, its state, its calls (design §5.2).

A server is run by one task. The MCP SDK uses ``anyio`` cancel scopes, and a scope must end in the
task that began it, so the task opens the transport, starts the session, and then waits until it
is told to stop; closing the transport is the last thing it does. Tool calls come from other
tasks. They only pass through memory streams, which is allowed.

States::

    new -> starting -> ready -> stopped / closed
              |          |
              v          v
            failed      dead        (dead: a call found the server gone, or the task failed)

A server that is ``dead`` is connected again by the next tool call, at most
``MAX_AUTO_RECONNECTS`` times in a row (a call that gets an answer starts the count again). So is
a ``failed`` one: only a server that has been ready has tools to call, so a ``failed`` server
that gets a call is one that did not come back. ``/mcp reconnect`` has no such limit.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import anyio
from langchain_mcp_adapters.tools import load_mcp_tools

from pi_agent_core.adapters.langchain_tools import from_langchain_tool
from pi_agent_core.types import AgentTool, AgentToolResult
from pi_mcp.errors import (
    CallFailed,
    ConnectionEnded,
    ConnectionUnavailable,
    StartupTimeout,
    connection_lost,
    describe_error,
    error_text_for_model,
    first_leaf,
    is_command_not_found,
)
from pi_mcp.redact import secrets_of
from pi_mcp.settings import CLOSE_GRACE, MAX_AUTO_RECONNECTS, PING_TIMEOUT, Settings
from pi_mcp.transport import executable_name, open_server_log, open_session

if TYPE_CHECKING:
    from mcp import ClientSession

    from pi_agent_core.extensions import ClientMcpServer

logger = logging.getLogger(__name__)

State = Literal["new", "starting", "ready", "failed", "dead", "stopped", "closed"]

ReadyCallback = Callable[["ServerConnection", dict[str, AgentTool]], None]
EventCallback = Callable[["ServerConnection", str, str], None]


async def _cancel_when(event: asyncio.Event, scope: anyio.CancelScope) -> None:
    await event.wait()
    scope.cancel()


async def _shielded(future: asyncio.Future[Any]) -> Any:
    """Wait for *future* without cancelling it when the waiter is cancelled (it is shared)."""
    return await asyncio.shield(future)


class ServerConnection:
    """The connection to one server that the client passed."""

    def __init__(
        self,
        server: ClientMcpServer,
        *,
        cwd: str | None,
        home: Path,
        settings: Settings,
        on_ready: ReadyCallback,
        on_event: EventCallback,
    ) -> None:
        self.server = server
        self.name = server.name
        self.state: State = "new"
        self.error: str | None = None
        """Why the server failed or stopped, for the user: short, one line, no secret."""
        self.log_path: Path | None = None
        self.barrier_spent = False
        """The first turn did wait for this server once; later turns do not wait again."""
        self._cwd = cwd if cwd and os.path.isdir(cwd) else None
        self._home = home
        self._settings = settings
        self._on_ready = on_ready
        self._on_event = on_event
        self._secrets = secrets_of(server)
        self._inner: dict[str, AgentTool] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._gone: asyncio.Future[BaseException | None] | None = None
        """Done when the task of the current start has ended, for whatever reason. Its result is
        what ended it (``None``: it was told to stop)."""
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._closing = False
        self._auto_reconnects = 0
        self._generation = 0
        """Counts the starts of the task, so a call that waited for a reconnect can tell that
        someone else made it, and not make a second one."""

    # -- what the user can ask -------------------------------------------------------------

    @property
    def tool_count(self) -> int:
        return len(self._inner)

    @property
    def is_starting(self) -> bool:
        return self.state == "starting"

    async def wait_started(self) -> None:
        """Return when the server is ready, or when it failed (or was stopped)."""
        await self._started.wait()

    # -- life ------------------------------------------------------------------------------

    def start(self) -> None:
        """Begin to connect. This starts a task and returns: nothing is awaited here."""
        if self._closing or self.state in ("starting", "ready"):
            return
        self._launch()

    def _launch(self) -> None:
        self._generation += 1
        self.state = "starting"
        self.error = None
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._gone = loop.create_future()
        self._task = loop.create_task(
            self._run(self._generation, self._stop, self._started, self._gone),
            name=f"pi-mcp:{self.name}",
        )

    async def _run(
        self,
        generation: int,
        stop: asyncio.Event,
        started: asyncio.Event,
        gone: asyncio.Future[BaseException | None],
    ) -> None:
        errlog = None
        if self.server.transport == "stdio":
            errlog, self.log_path = open_server_log(self._home, self.name)
        failure: BaseException | None = None
        try:
            async with open_session(self.server, cwd=self._cwd, errlog=errlog) as session:
                tools = await self._start_up(session, stop)
                if tools is not None:
                    self._session = session
                    self._publish(tools)
                    self.state = "ready"
                    started.set()
                    await stop.wait()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # the SDK raises ExceptionGroups
            failure = exc
        finally:
            if errlog is not None:
                errlog.close()
            if generation == self._generation:
                # (a task that was cancelled late must not overwrite the state of its successor)
                self._session = None
                self._finish(failure)
            started.set()
            if not gone.done():
                # Calls that wait for an answer stop waiting. The SDK does not tell them when a
                # transport ends by cancellation, and they would wait for the call timeout.
                gone.set_result(failure)

    async def _start_up(self, session: ClientSession, stop: asyncio.Event) -> list[Any] | None:
        """``initialize`` and the tool list, with a time limit. ``None``: told to stop meanwhile.

        The limit is a cancel scope around these two steps only. The transport is closed by the
        caller, outside the scope, so the child process is ended the ordinary way and not while
        a cancellation is going on.
        """
        scope = anyio.CancelScope(deadline=anyio.current_time() + self._settings.startup_timeout)
        watcher = asyncio.get_running_loop().create_task(_cancel_when(stop, scope))
        tools: list[Any] | None = None
        try:
            with scope:
                await session.initialize()
                tools = await load_mcp_tools(session, server_name=self.name)
        finally:
            watcher.cancel()
        if scope.cancelled_caught or tools is None:
            if stop.is_set():
                return None
            raise StartupTimeout(self._settings.startup_timeout)
        return tools

    def _publish(self, lc_tools: list[Any]) -> None:
        """Wrap the tools of this session and hand them to the owner, which registers them."""
        inner: dict[str, AgentTool] = {}
        for lc_tool in lc_tools:
            try:
                inner[lc_tool.name] = from_langchain_tool(lc_tool)
            except Exception:
                logger.warning("MCP server %r: a tool could not be wrapped; skipped", self.name)
        self._inner = inner
        try:
            self._on_ready(self, dict(inner))
        except Exception:
            logger.warning("MCP server %r: registering its tools failed", self.name, exc_info=True)

    def _finish(self, failure: BaseException | None) -> None:
        """Set the state when the task ends. A ``dead`` or ``failed`` server stays so."""
        if failure is not None:
            was_ready = self.state == "ready"
            self.error = self._reason(failure)
            self.state = "dead" if was_ready else "failed"
            if not was_ready:
                try:
                    self._on_event(self, "failed", self._failure_notice())
                except Exception:
                    logger.warning("MCP server %r: the notice could not be sent", self.name)
            return
        if self.state in ("starting", "ready"):
            self.state = "closed" if self._closing else "stopped"

    def _reason(self, exc: BaseException) -> str:
        leaf = first_leaf(exc)
        if isinstance(leaf, StartupTimeout):
            return str(leaf)
        if is_command_not_found(exc):
            return f'command not found: "{executable_name(self.server)}". Check that it is on PATH.'
        return describe_error(exc, self._secrets)

    def _failure_notice(self) -> str:
        text = f'MCP server "{self.name}" did not start: {self.error}'
        if not text.endswith("."):
            text += "."
        text += " Its tools are not available in this session."
        if self.log_path is not None:
            text += f" Log: {self.log_path}"
        return text

    async def _stop_task(self) -> None:
        """Ask the task to end, give it ``CLOSE_GRACE``, then cancel it."""
        task = self._task
        if task is None or task.done():
            return
        self._stop.set()
        done, _ = await asyncio.wait({task}, timeout=CLOSE_GRACE)
        if not done:
            task.cancel()
            await asyncio.wait({task}, timeout=CLOSE_GRACE)

    async def close(self) -> None:
        """End the server for good (the session is over)."""
        self._closing = True
        await self._stop_task()
        if self.state != "failed":
            self.state = "closed"
        self._started.set()

    async def reconnect(self) -> None:
        """Connect again, whatever the state is (``/mcp reconnect``). Waits for the result.

        When it works, the user has stepped in, so the reconnects that calls start by themselves
        count from zero again.
        """
        async with self._lock:
            if self._closing:
                raise ConnectionUnavailable(f'MCP server "{self.name}" is closed.')
            await self._restart()
            if self.state == "ready":
                self._auto_reconnects = 0

    async def _restart(self) -> None:
        await self._stop_task()
        if self._closing:  # the session ended while the old task was stopping
            return
        self._launch()
        await self._started.wait()

    def mark_dead(self, reason: str) -> None:
        """A call found the server gone. The next call connects it again."""
        if self.state != "ready":
            return
        self.state = "dead"
        self.error = reason
        self._stop.set()  # the task ends and closes what is left of the transport

    async def probe(self) -> None:
        """Ping a server that should be ready. A server that is gone becomes ``dead``.

        A server that is only slow (it is busy with a call) is left as it is.
        """
        session = self._session
        if self.state != "ready" or session is None:
            return
        try:
            with anyio.fail_after(PING_TIMEOUT):
                await session.send_ping()
        except TimeoutError:
            return
        except Exception as exc:
            if connection_lost(exc) is not None:
                self.mark_dead(describe_error(exc, self._secrets))

    # -- calls -----------------------------------------------------------------------------

    async def ensure_ready(self) -> None:
        """Make sure a call can go out: wait for a start in progress, connect a dead server."""
        if self.state == "starting":
            await self._started.wait()
        if self.state == "ready":
            return
        # "failed" counts too: only a server that was ready has tools to call, so a failed one
        # that gets a call is one that did not come back.
        if self.state in ("dead", "stopped", "failed"):
            await self._reconnect_by_call()
            return
        raise ConnectionUnavailable(self._unavailable())

    async def _reconnect_by_call(self) -> None:
        seen = self._generation
        async with self._lock:
            if self._closing:
                raise ConnectionUnavailable(f'MCP server "{self.name}" is closed.')
            if self._generation == seen:  # nobody else connected it while this call waited
                if self._auto_reconnects >= MAX_AUTO_RECONNECTS:
                    text = (
                        f'MCP server "{self.name}" stopped and cannot reconnect by itself. '
                        f"Use /mcp reconnect {self.name}."
                    )
                    self._on_event(self, "exhausted", text)
                    raise ConnectionUnavailable(text)
                self._auto_reconnects += 1
                await self._restart()
        if self.state == "ready":
            return
        text = (
            f'MCP server "{self.name}" stopped and did not come back: {self.error}. '
            f"Use /mcp reconnect {self.name}."
        )
        self._on_event(self, "not-back", text)
        raise ConnectionUnavailable(text)

    def _unavailable(self) -> str:
        if self.state == "closed":
            return f'MCP server "{self.name}" is closed.'
        return f'MCP server "{self.name}" is not connected ({self.state}).'

    async def call(
        self, tool: str, tool_call_id: str, params: Any, signal: Any | None
    ) -> AgentToolResult:
        """Call *tool* of this server and return what the SDK gives back, unshaped.

        The call has a time limit (``Settings.call_timeout``) and ends when the user aborts. A
        server that is gone is connected again, and a request that never left is sent again, once.
        An error text goes through ``redact`` before it is raised.
        """
        retried = False
        while True:
            if getattr(signal, "aborted", False):
                raise CallFailed("Operation aborted")
            await self.ensure_ready()
            inner = self._inner.get(tool)
            if inner is None:
                raise RuntimeError(
                    f'MCP server "{self.name}" does not offer the tool "{tool}" any more.'
                )
            try:
                result = await self._invoke(inner, tool, tool_call_id, params, signal)
            except CallFailed:
                raise
            except Exception as exc:
                ended = isinstance(exc, ConnectionEnded)
                cause = exc.failure if isinstance(exc, ConnectionEnded) and exc.failure else exc
                lost = connection_lost(cause) or ("lost" if ended else None)
                if lost is None:
                    self._auto_reconnects = 0  # the server answered, with an error
                    raise RuntimeError(error_text_for_model(exc, self._secrets)) from None
                reason = describe_error(cause, self._secrets)
                self.mark_dead(reason)
                if lost == "unsent" and not retried:
                    retried = True
                    continue
                raise RuntimeError(
                    f'MCP server "{self.name}" stopped while the tool ran: {reason}. '
                    "The next call tries to connect it again."
                ) from None
            self._auto_reconnects = 0  # it works again: the next stop is a new case
            return result

    async def _invoke(
        self, inner: AgentTool, tool: str, tool_call_id: str, params: Any, signal: Any | None
    ) -> AgentToolResult:
        """Run the call in a task of its own, against the time limit, the abort signal and the
        end of the connection (whichever comes first)."""
        work = asyncio.ensure_future(inner.execute(tool_call_id, params, None, None))
        racers: set[asyncio.Future[Any]] = {work}
        ended: asyncio.Future[Any] | None = None
        if self._gone is not None:
            ended = asyncio.ensure_future(_shielded(self._gone))
            racers.add(ended)
        abort: asyncio.Future[Any] | None = None
        wait_aborted = getattr(signal, "wait_aborted", None)
        if callable(wait_aborted):
            abort = asyncio.ensure_future(wait_aborted())
            racers.add(abort)
        try:
            done, _ = await asyncio.wait(
                racers, timeout=self._settings.call_timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if work in done:
                return work.result()
            if abort is not None and abort in done:
                raise CallFailed("Operation aborted")
            if ended is not None and ended in done:
                raise ConnectionEnded(ended.result())
            raise CallFailed(
                f'MCP server "{self.name}" did not answer the call of "{tool}" within '
                f"{self._settings.call_timeout:g} s."
            )
        finally:
            for racer in racers:
                if not racer.done():
                    racer.cancel()
            await asyncio.gather(*racers, return_exceptions=True)
