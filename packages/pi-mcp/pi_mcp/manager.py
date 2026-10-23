"""The MCP servers of one session, and what the model and the user get from them (design §5).

One ``Manager`` belongs to one session (one harness). It owns a ``ServerConnection`` for every
server that the client passed, registers their tools, holds the first turn back while a server
starts, and answers ``/mcp``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pi_agent_core.extensions import ToolDefinition
from pi_agent_core.types import AgentTool, AgentToolResult
from pi_mcp.connection import ServerConnection
from pi_mcp.errors import ConnectionUnavailable
from pi_mcp.settings import BARRIER_WAIT, Settings
from pi_mcp.tools import public_name, shape_result
from pi_mcp.transport import describe_transport

if TYPE_CHECKING:
    from pi_agent_core.extensions import ClientMcpServer, ExtensionAPI

logger = logging.getLogger(__name__)

NO_SERVERS = (
    "The client passed no MCP servers for this session. If you set some up in your editor, "
    "start a new thread: some editors send the list before the project has loaded."
)


class Manager:
    def __init__(
        self, pi: ExtensionAPI, servers: Sequence[ClientMcpServer], *, settings: Settings
    ) -> None:
        self._pi = pi
        self._taken: set[str] = set()
        self._public: dict[tuple[str, str], str] = {}
        """(server, tool of the server) -> the name the model sees. A tool is registered once;
        a reconnect makes the same tool call the new session (through its ``ServerConnection``)."""
        self._said: set[tuple[str, str]] = set()
        self._started = False
        try:
            cwd: str | None = pi.cwd
        except RuntimeError:
            cwd = None
        self.connections: dict[str, ServerConnection] = {}
        self._repeated: list[str] = []
        for server in servers:
            if server.name in self.connections:  # a client bug: the first one is used
                self._repeated.append(server.name)
                continue
            self.connections[server.name] = ServerConnection(
                server,
                cwd=cwd,
                home=pi.home,
                settings=settings,
                on_ready=self._on_ready,
                on_event=self._on_event,
            )

    # -- life ------------------------------------------------------------------------------

    def start(self) -> None:
        """Start every server. Each one runs in a task of its own; nothing is awaited."""
        if self._started:
            return
        self._started = True
        for name in self._repeated:
            self._say(
                name,
                "repeated",
                f'The client passed the MCP server name "{name}" twice. Only the first is used.',
            )
        for connection in self.connections.values():
            connection.start()

    async def close(self) -> None:
        """End every server (the session is over). One that fails to end does not stop the rest."""
        results = await asyncio.gather(
            *(connection.close() for connection in self.connections.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("An MCP server could not be closed cleanly: %s", type(result))

    async def wait_started(self) -> None:
        """The turn barrier: wait until the servers that are starting are ready, or give up.

        The first turn waits at most ``BARRIER_WAIT`` seconds, so that it sees the tools of a
        server that starts quickly. A server that needs longer goes on in the background, its
        tools join in a later turn, and later turns do not wait for it again. Returns at once
        when nothing is starting. Only the waiting is cancelled when the turn is: the servers
        go on.
        """
        waiting = [c for c in self.connections.values() if c.is_starting and not c.barrier_spent]
        if not waiting:
            return
        watchers = [asyncio.ensure_future(c.wait_started()) for c in waiting]
        try:
            await asyncio.wait(watchers, timeout=BARRIER_WAIT)
        finally:
            for watcher in watchers:
                watcher.cancel()
        for connection in waiting:
            if connection.is_starting:
                connection.barrier_spent = True
                self._on_event(
                    connection,
                    "slow",
                    f'MCP server "{connection.name}" is still starting. '
                    "Its tools join in a later turn.",
                )

    # -- what the connections report -------------------------------------------------------

    def _on_event(self, connection: ServerConnection, kind: str, text: str) -> None:
        self._say(connection.name, kind, text)

    def _say(self, name: str, kind: str, text: str) -> None:
        """Tell the user, once for each server and kind of event."""
        key = (name, kind)
        if key in self._said:
            return
        self._said.add(key)
        try:
            self._pi.notify(text)
        except Exception:
            logger.warning("MCP server %r: a notice could not be sent", name)

    def _on_ready(self, connection: ServerConnection, tools: dict[str, AgentTool]) -> None:
        """Register the tools of a server that is ready (new ones only, after a reconnect)."""
        for tool_name, tool in tools.items():
            key = (connection.name, tool_name)
            if key in self._public:
                continue
            name = public_name(connection.name, tool_name, self._taken)
            try:
                self._pi.register_tool(self._definition(connection, tool_name, name, tool))
            except Exception:
                self._taken.discard(name)
                logger.warning(
                    "MCP server %r: the tool %r could not be registered", connection.name, tool_name
                )
                continue
            self._public[key] = name

    @staticmethod
    def _definition(
        connection: ServerConnection, tool_name: str, name: str, tool: AgentTool
    ) -> ToolDefinition:
        async def execute(
            tool_call_id: str, params: Any, signal: Any | None = None, on_update: Any | None = None
        ) -> AgentToolResult:
            result = await connection.call(tool_name, tool_call_id, params, signal)
            return shape_result(result, server=connection.name, tool=tool_name)

        description = (tool.description or "").strip()
        return ToolDefinition(
            name=name,
            description=description
            or f'The tool "{tool_name}" of the MCP server "{connection.name}".',
            parameters=tool.parameters,
            execute=execute,
            label=f"{connection.name}: {tool_name}",
            annotations=getattr(tool, "annotations", None),
        )

    # -- /mcp ------------------------------------------------------------------------------

    async def command(self, args: str) -> str:
        """The text for ``/mcp`` (the servers) and ``/mcp reconnect <name>``."""
        words = args.split()
        if not words:
            return await self.status()
        if words[0] != "reconnect":
            return "Use /mcp to see the servers, or /mcp reconnect <name> to connect one again."
        name = " ".join(words[1:])
        if not name:
            return "Use /mcp reconnect <name>."
        connection = self.connections.get(name)
        if connection is None:
            known = ", ".join(self.connections) or "none"
            return f'No MCP server is named "{name}". The servers are: {known}.'
        try:
            await connection.reconnect()
        except ConnectionUnavailable as exc:
            return str(exc)
        return self._line(connection)

    async def status(self) -> str:
        if not self.connections:
            return NO_SERVERS
        await asyncio.gather(
            *(connection.probe() for connection in self.connections.values()),
            return_exceptions=True,
        )
        lines = [f"MCP servers ({len(self.connections)}):"]
        lines.extend(self._line(connection) for connection in self.connections.values())
        return "\n".join(lines)

    @staticmethod
    def _line(connection: ServerConnection) -> str:
        how = describe_transport(connection.server)
        state = connection.state
        if state == "ready":
            count = connection.tool_count
            detail = f"ready, {count} tool{'' if count == 1 else 's'}"
        elif state == "starting":
            detail = "starting"
        elif state == "failed":
            detail = f"failed: {connection.error}"
            if connection.log_path is not None:
                detail += f" (log: {connection.log_path})"
        elif state == "dead":
            detail = f"stopped: {connection.error}. The next tool call connects it again"
        else:
            detail = state
        return f"- {connection.name}: {how}, {detail}"
