"""pi-mcp: connect the MCP servers that an ACP client passes (design: docs/specs/2026-10-23-mcp-
extension-design.md).

An editor such as Zed hands the agent its configured MCP servers in ``session/new``,
``session/load`` and ``session/resume``. The agent has no MCP client of its own. This extension
connects them: stdio, http and sse servers, one connection per server and session. Their tools
reach the model as ``mcp__<server>__<tool>``; ``/mcp`` shows the state of the servers.

Install: ``pip install pi-mcp-py`` (or ``pip install -e ./packages/pi-mcp`` from a checkout of the
repository). Nothing is configured here: the servers come from the editor.

Environment variables:
  - ``PI_MCP_STARTUP_TIMEOUT`` -- seconds a server may take to start (default 60)
  - ``PI_MCP_CALL_TIMEOUT``    -- seconds a tool call may take (default 120)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

__all__ = ["activate"]

logger = logging.getLogger(__name__)


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point: take over the servers the client passed, start them later.

    No I/O happens here (a failing ``activate`` is rolled back, and a started child process
    would not be). The servers start in the ``session_start`` hook, each in a task of its own.
    """
    try:
        servers = tuple(pi.client_mcp_servers)
    except AttributeError:
        logger.warning(
            "pi-mcp needs a newer pi-agent-core-lc (0.6.0 or later): this one does not hand over "
            "the MCP servers of the client. pi-mcp does nothing."
        )
        return
    except RuntimeError:  # no harness behind this API (a test, a bare registry)
        servers = ()

    from pi_mcp.manager import Manager
    from pi_mcp.settings import Settings

    manager = Manager(pi, servers, settings=Settings.from_env())
    if servers:
        pi.claim_client_mcp_servers([server.name for server in servers])

        def on_session_start(_event: Any) -> None:
            try:
                manager.start()
            except Exception:  # a handler that raises would fail the load of every extension
                logger.warning("pi-mcp could not start the MCP servers", exc_info=True)

        pi.on("session_start", on_session_start)
        pi.add_turn_barrier(manager.wait_started)
        pi.register_cleanup(manager.close)

    async def mcp_command(args: str) -> None:
        pi.send_message(await manager.command(args))

    pi.register_command(
        "mcp",
        description="Show the MCP servers of this session (/mcp reconnect <name> to reconnect)",
        handler=mcp_command,
    )
