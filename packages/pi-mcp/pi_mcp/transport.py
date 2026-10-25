"""Opening a session with an MCP server: a child process (stdio) or a URL (http, sse).

stdio is done here, with the MCP SDK, and not with ``create_session`` of
``langchain-mcp-adapters`` (design §6.2). That function expands ``${VAR}`` in the ``env`` values
from the environment of *this* process, which holds the LLM key of the user, and for a value that
stays unexpanded it writes the whole value (a token, maybe) into a warning. http and sse expand
nothing, so they use ``create_session``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TextIO
from urllib.parse import urlsplit

import httpx
from langchain_mcp_adapters.sessions import create_session
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pi_mcp.settings import MAX_LOG_BYTES

if TYPE_CHECKING:
    from pi_agent_core.extensions import ClientMcpServer

_HTTP_TIMEOUT = 30.0
_HTTP_READ_TIMEOUT = 300.0
"""The limits of the MCP SDK for its HTTP client: 30 s to connect and write, 5 minutes to read
(a server may hold a response stream open)."""

# What a server gets from this process, besides the allow-list of the MCP SDK (HOME, PATH, ...).
# The LLM keys of the user are not in it. A server behind a proxy needs the proxy variables
# (``npx`` downloads its package through them), a company CA needs the certificate variables.
_FORWARDED = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "LANG",
    "LC_ALL",
    "TZ",
)
_FORWARDED_WINDOWS = (
    "COMSPEC",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMDATA",
    "TMP",
    "ALLUSERSPROFILE",
)


def child_env(server: ClientMcpServer, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment variables to give a stdio server, on top of the MCP SDK's own list.

    The forwarded variables of this process, then the ``env`` of the client (which wins). Nothing
    is expanded: ``${HOME}`` in a value stays the text ``${HOME}``.
    """
    source = os.environ if environ is None else environ
    windows = sys.platform == "win32"
    names = _FORWARDED + (_FORWARDED_WINDOWS if windows else ())
    env: dict[str, str] = {}
    seen: set[str] = set()
    for name in names:
        value = source.get(name)
        key = name.upper() if windows else name
        if value is None or key in seen:
            continue
        seen.add(key)
        env[name] = value
    env.update(server.env)
    return env


def executable_name(server: ClientMcpServer) -> str:
    """The file name of the command of a stdio server, without its directory or arguments."""
    return PurePosixPath(server.command.replace("\\", "/")).name or server.command


def host_name(server: ClientMcpServer) -> str:
    """The host of the URL of a remote server (no port, path, query or credentials)."""
    try:
        return urlsplit(server.url).hostname or "?"
    except ValueError:
        return "?"


def is_loopback(url: str) -> bool:
    """Whether *url* names this machine (``localhost``, ``127.0.0.1``, ``::1``)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def direct_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """The HTTP client of the MCP SDK, but it ignores the proxy settings of the system.

    Used for a server on this machine. ``httpx`` reads the proxy from the environment, and on
    Windows and macOS from the system settings too, and it does not skip loopback addresses as
    other HTTP clients do. A local MCP server would then be reached through the proxy of the user.
    Redirects stay off, as in the SDK (its transports follow them within the origin).
    """
    kwargs: dict[str, Any] = {
        "timeout": timeout or httpx.Timeout(_HTTP_TIMEOUT, read=_HTTP_READ_TIMEOUT),
        "trust_env": False,
    }
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def describe_transport(server: ClientMcpServer) -> str:
    """``stdio (npx)`` or ``http (example.com)``: all that may be shown of how a server runs."""
    if server.transport == "stdio":
        return f"stdio ({executable_name(server)})"
    return f"{server.transport} ({host_name(server)})"


def log_stem(name: str) -> str:
    """A file name for the log of the server *name*: safe, and the same name gives the same one."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80] or "server"
    if safe != name:
        safe += "-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
    return safe


def open_server_log(home: Path, name: str) -> tuple[TextIO, Path | None]:
    """Open the file that gets the stderr of a stdio server. Returns it and its path.

    A talkative server must not write into the stderr of this process: the client reads it, and a
    full pipe stops the agent. The file is appended to (two sessions can run the same server) and
    started over when it has grown large. The user can read it: ``<home>/logs/mcp/<name>.log``.
    Without a usable file the output is dropped.
    """
    path = home / "logs" / "mcp" / f"{log_stem(name)}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        too_big = path.exists() and path.stat().st_size > MAX_LOG_BYTES
        stream = open(path, "w" if too_big else "a", encoding="utf-8", errors="replace")  # noqa: SIM115
    except OSError:
        return open(os.devnull, "w"), None
    try:
        if sys.platform != "win32":
            os.chmod(path, 0o600)  # a server may print what it was given
        stream.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} start ---\n")
        stream.flush()
    except OSError:
        pass
    return stream, path


@asynccontextmanager
async def open_session(
    server: ClientMcpServer, *, cwd: str | None = None, errlog: TextIO | None = None
) -> AsyncIterator[ClientSession]:
    """Connect to *server*. The session is not initialized yet.

    Enter and leave this in one task: the MCP SDK uses ``anyio`` cancel scopes, which must end in
    the task that began them.
    """
    if server.transport == "stdio":
        params = StdioServerParameters(
            command=server.command, args=list(server.args), env=child_env(server), cwd=cwd
        )
        kwargs: dict[str, Any] = {} if errlog is None else {"errlog": errlog}
        async with (
            stdio_client(params, **kwargs) as (read, write),
            ClientSession(read, write) as session,
        ):
            yield session
        return
    connection: dict[str, Any] = {"transport": server.transport, "url": server.url}
    if server.headers:
        connection["headers"] = dict(server.headers)
    if is_loopback(server.url):
        connection["httpx_client_factory"] = direct_client
    async with create_session(connection) as session:  # type: ignore[arg-type]
        yield session
