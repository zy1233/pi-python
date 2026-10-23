"""The MCP servers a client passes, as plain data for the extensions.

ACP lets a client hand an agent MCP servers in ``session/new``, ``session/load`` and
``session/resume``. This agent has no MCP client of its own (``docs/specs/2026-10-23-mcp-
extension-design.md``). It turns the list into ``ClientMcpServer`` records, gives them to the
harness, and an extension that wants them (the optional ``pi-mcp-py`` package) claims and
connects them. The servers that nobody claims are named in a notice (``mcp_notice``).

A server's ``env``, ``headers``, ``args`` and ``url`` routinely hold tokens. Nothing here logs
them: a record skipped for being malformed is named, and that is all.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from importlib.metadata import entry_points
from typing import Any

from pi_agent_core.extensions import ClientMcpServer

logger = logging.getLogger(__name__)

# The entry point of the extension that connects the client's MCP servers. Only its metadata is
# read (``mcp_extension_installed``); this package never imports it.
MCP_EXTENSION_ENTRY_POINT = "pi-mcp"
_EXTENSION_GROUP = "pi_agent.extensions"


def mcp_extension_installed() -> bool:
    """Whether the ``pi-mcp`` extension is installed.

    The answer decides if ``initialize`` says that the agent takes ``http`` and ``sse`` servers
    (stdio is always allowed in ACP), and which notice the user gets about servers nobody took.
    Reads package metadata only: nothing is imported, so a broken extension still counts.
    """
    try:
        return any(
            point.name == MCP_EXTENSION_ENTRY_POINT
            for point in entry_points(group=_EXTENSION_GROUP)
        )
    except Exception:
        logger.debug("Could not read the entry points of %s", _EXTENSION_GROUP, exc_info=True)
        return False


def client_mcp_servers(servers: Sequence[Any] | None) -> list[ClientMcpServer]:
    """Turn what the client passed (ACP models or plain dicts) into ``ClientMcpServer`` records.

    Names are made usable: a missing or blank name becomes ``server-<position>``, and a name
    that an earlier server has already gets ``-2``, ``-3`` ... so a name identifies one server
    in a session. A server that cannot be used (a stdio server with no command, a remote one
    with no URL, a type this code does not know) is skipped with a warning that names it.
    """
    records: list[ClientMcpServer] = []
    taken: set[str] = set()
    for position, server in enumerate(servers or (), start=1):
        name = _unique_name(_clean_name(_field(server, "name")) or f"server-{position}", taken)
        record = _convert(server, name)
        if record is None:
            continue
        taken.add(name)
        records.append(record)
    return records


def _convert(server: Any, name: str) -> ClientMcpServer | None:
    kind = _field(server, "type")
    if kind in ("http", "sse"):
        url = _field(server, "url")
        if not isinstance(url, str) or not url.strip():
            logger.warning("Skipping the MCP server %r the client passed: it has no URL", name)
            return None
        return ClientMcpServer(
            name=name,
            transport=kind,
            url=url.strip(),
            headers=_pairs(_field(server, "headers")),
        )
    command = _field(server, "command")
    if kind in (None, "stdio") and isinstance(command, str) and command.strip():
        args = _field(server, "args")
        return ClientMcpServer(
            name=name,
            transport="stdio",
            command=command.strip(),
            args=tuple(str(arg) for arg in args) if isinstance(args, (list, tuple)) else (),
            env=_pairs(_field(server, "env")),
        )
    reason = "it has no command" if kind in (None, "stdio") else f"its type {kind!r} is not known"
    logger.warning("Skipping the MCP server %r the client passed: %s", name, reason)
    return None


def _field(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)


def _pairs(items: Any) -> tuple[tuple[str, str], ...]:
    """``[{name, value}, ...]`` (ACP's ``EnvVariable`` / ``HttpHeader``) or a mapping, as pairs."""
    if isinstance(items, Mapping):
        return tuple((str(key), str(value)) for key, value in items.items())
    pairs: list[tuple[str, str]] = []
    for item in items if isinstance(items, (list, tuple)) else ():
        key, value = _field(item, "name"), _field(item, "value")
        if isinstance(key, str) and key and value is not None:
            pairs.append((key, str(value)))
    return tuple(pairs)


def _clean_name(name: Any) -> str:
    """One line of printable text: a name goes into logs, notices and tool names."""
    if not isinstance(name, str):
        return ""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in name).split())


def _unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    suffix = 2
    while f"{name}-{suffix}" in taken:
        suffix += 1
    return f"{name}-{suffix}"
