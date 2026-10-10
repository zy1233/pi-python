"""Saying that the MCP servers a client passed are not used (plan 1.P4).

ACP lets a client hand an agent MCP servers in ``session/new``, ``session/load`` and
``session/resume``, and says an agent MUST support stdio ones. This agent connects to none: MCP is
outside what pi-python's coding agent does (Phase 4 non-goal), and tools come from the harness and
its extensions. Ignoring them silently would leave the user wondering why the tools of the server
they set up in their editor are missing, so the agent says so once when the session is set up.

Only the *names* go into the notice and the log: a server's ``env``, ``headers``, ``args`` and
``url`` routinely hold tokens.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# A client with a long list gets the first few names and a count, not a screenful.
_MAX_NAMES = 8
_MAX_NAME_CHARS = 60


def mcp_server_names(servers: Sequence[Any] | None) -> list[str]:
    """The display names of the servers the client passed, in order (``[]``: none)."""
    names: list[str] = []
    for server in servers or ():
        name = server.get("name") if isinstance(server, dict) else getattr(server, "name", None)
        names.append(_one_line(name) if isinstance(name, str) and name.strip() else "(unnamed)")
    return names


def ignored_mcp_servers_notice(names: Sequence[str]) -> str | None:
    """The message for the user: what was passed, and that it is not used (``None``: nothing)."""
    if not names:
        return None
    shown = ", ".join(names[:_MAX_NAMES])
    if len(names) > _MAX_NAMES:
        shown += f", and {len(names) - _MAX_NAMES} more"
    noun = "server" if len(names) == 1 else "servers"
    return (
        f"This agent does not connect to MCP servers, so the {len(names)} MCP {noun} the client "
        f"passed ({shown}) were ignored and their tools are not available in this session."
    )


def _one_line(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > _MAX_NAME_CHARS:
        line = line[: _MAX_NAME_CHARS - 3].rstrip() + "..."
    return line
