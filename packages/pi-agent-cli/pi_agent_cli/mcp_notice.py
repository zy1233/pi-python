"""Saying that the MCP servers a client passed were not connected (plan 1.P4).

ACP lets a client hand an agent MCP servers in ``session/new``, ``session/load`` and
``session/resume``, and says an agent MUST support stdio ones. This agent has no MCP client of
its own. The optional ``pi-mcp-py`` extension connects the servers (``client_mcp`` hands them
over, and the extension claims them). A server that no extension claimed is not connected, and
saying nothing would leave the user wondering why the tools of the server they set up in their
editor are missing. So the agent says so once when the session is set up.

Only the *names* go into the notice and the log: a server's ``env``, ``headers``, ``args`` and
``url`` routinely hold tokens.
"""

from __future__ import annotations

from collections.abc import Sequence

# A client with a long list gets the first few names and a count, not a screenful.
_MAX_NAMES = 8
_MAX_NAME_CHARS = 60


def ignored_mcp_servers_notice(
    names: Sequence[str], *, extension_installed: bool = False
) -> str | None:
    """The message for the user: what was passed, and that it was not connected.

    *extension_installed*: ``pi-mcp-py`` is installed, so the user needs no hint to install it;
    nothing took these servers over, and a failed extension has been reported on its own.
    ``None`` when there is nothing to say.
    """
    if not names:
        return None
    lines = [_one_line(name) for name in names]
    shown = ", ".join(lines[:_MAX_NAMES])
    if len(lines) > _MAX_NAMES:
        shown += f", and {len(lines) - _MAX_NAMES} more"
    single = len(lines) == 1
    noun = "server" if single else "servers"
    it, its = ("it", "its") if single else ("them", "their")
    lead = f"The client passed {len(lines)} MCP {noun} ({shown})."
    if extension_installed:
        return (
            f"{lead} No extension took {it} over, so {its} tools are not available in this session."
        )
    return (
        f"{lead} This agent does not connect to MCP servers by itself, so {its} tools are not "
        f"available in this session. To use {it}, install the pi-mcp-py package."
    )


def _one_line(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > _MAX_NAME_CHARS:
        line = line[: _MAX_NAME_CHARS - 3].rstrip() + "..."
    return line
