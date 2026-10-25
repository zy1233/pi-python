"""A small MCP server for the tests of pi-mcp.

    python mock_server.py                  # stdio (the way a client starts it)
    python mock_server.py http PORT        # Streamable HTTP on 127.0.0.1:PORT (/mcp)
    python mock_server.py sse PORT         # SSE on 127.0.0.1:PORT (/sse)

The tools are written for the tests: each one gives a test something to look at.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.types import CallToolResult, ToolAnnotations


def build(port: int = 0) -> FastMCP:
    mcp = FastMCP(
        "pi-mcp-test", host="127.0.0.1", port=port, stateless_http=True, json_response=True
    )

    @mcp.tool()
    def echo(text: str) -> str:
        """Give the text back."""
        return f"echo: {text}"

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @mcp.tool()
    async def slow(seconds: float) -> str:
        """Answer after some seconds."""
        await asyncio.sleep(seconds)
        return "slow done"

    @mcp.tool()
    def fail(message: str) -> str:
        """Fail with the message (an MCP ``isError`` result)."""
        raise ValueError(message)

    @mcp.tool()
    def big(size: int) -> str:
        """A text of this many characters."""
        return "x" * size

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def readonly() -> str:
        """A tool that says it only reads."""
        return "read"

    @mcp.tool()
    def env_names() -> str:
        """The names of the environment variables of this process, sorted, one per line."""
        return "\n".join(sorted(os.environ))

    @mcp.tool()
    def env_value(name: str) -> str:
        """The value of an environment variable, or <unset>."""
        return os.environ.get(name, "<unset>")

    @mcp.tool()
    def header(name: str, ctx: Context) -> str:
        """A header of the HTTP request that carries this call, or <unset>."""
        request = ctx.request_context.request
        if request is None:  # stdio: there is no request
            return "<no request>"
        return request.headers.get(name, "<unset>")

    @mcp.tool()
    def pid() -> str:
        """The process id of this server."""
        return str(os.getpid())

    @mcp.tool()
    def cwd() -> str:
        """The working directory of this server."""
        return os.getcwd()

    @mcp.tool()
    def crash() -> str:
        """End the process at once, without an answer."""
        os._exit(3)

    @mcp.tool()
    def structured() -> CallToolResult:
        """Only ``structuredContent``: no text."""
        return CallToolResult(content=[], structuredContent={"answer": 42, "items": ["a", "b"]})

    @mcp.tool()
    def image() -> Image:
        """A one-pixel picture."""
        return Image(data=b"\x89PNG\r\n\x1a\n", format="png")

    @mcp.tool()
    def noisy() -> str:
        """Write a line to stderr."""
        print("pi-mcp-test: a line on stderr", file=sys.stderr, flush=True)
        return "noisy done"

    @mcp.tool()
    def bash() -> str:
        """A tool that has the name of a built-in tool."""
        return "not the real bash"

    return mcp


def start_up() -> None:
    """What a test may ask of the start: the environment says (the client ``env`` carries it).

    - ``PI_MCP_TEST_PID_FILE``: write the process id to this file
    - ``PI_MCP_TEST_NEED_FILE``: end with exit code 1 unless this file exists
    - ``PI_MCP_TEST_EXIT``: end with exit code 2 after a line on stderr
    - ``PI_MCP_TEST_START_DELAY``: wait this many seconds before the server starts
    """
    pid_file = os.environ.get("PI_MCP_TEST_PID_FILE")
    if pid_file:
        with open(pid_file, "w") as handle:
            handle.write(str(os.getpid()))
    need_file = os.environ.get("PI_MCP_TEST_NEED_FILE")
    if need_file and not os.path.exists(need_file):
        sys.exit(1)
    if os.environ.get("PI_MCP_TEST_EXIT"):
        print("pi-mcp-test: refusing to start", file=sys.stderr, flush=True)
        sys.exit(2)
    delay = float(os.environ.get("PI_MCP_TEST_START_DELAY", "0"))
    if delay:
        time.sleep(delay)


if __name__ == "__main__":
    start_up()
    if len(sys.argv) > 2 and sys.argv[1] == "http":
        build(int(sys.argv[2])).run(transport="streamable-http")
    elif len(sys.argv) > 2 and sys.argv[1] == "sse":
        build(int(sys.argv[2])).run(transport="sse")
    else:
        build().run(transport="stdio")
