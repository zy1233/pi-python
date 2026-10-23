"""Remote servers (Streamable HTTP and SSE): the mock server runs as a separate process."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from mcp_support import MOCK_SERVER, WAIT, Lab, kill_process
from pi_mcp.transport import direct_client, is_loopback

from pi_agent_core.extensions import ClientMcpServer

TOKEN = "tok-abcdef-123456"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class HttpServer:
    """The mock server as a child process, on a port of this machine."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.port = free_port()
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{'mcp' if self.mode == 'http' else 'sse'}"

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(MOCK_SERVER), self.mode, str(self.port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise AssertionError("the mock server did not open its port")

    def stop(self) -> None:
        if self.process is not None:
            kill_process(self.process)
            self.process = None

    def client_view(self, name: str = "web", **changes: Any) -> ClientMcpServer:
        fields: dict[str, Any] = {
            "name": name,
            "transport": self.mode,
            "url": self.url,
            "headers": (("Authorization", f"Bearer {TOKEN}"),),
        }
        fields.update(changes)
        return ClientMcpServer(**fields)


@pytest.fixture(params=["http", "sse"])
def remote(request: pytest.FixtureRequest) -> Iterator[HttpServer]:
    server = HttpServer(request.param)
    server.start()
    try:
        yield server
    finally:
        server.stop()


class TestRemoteServer:
    async def test_the_tools_are_registered_and_called(self, lab: Lab, remote: HttpServer):
        await lab.start(remote.client_view())

        assert "mcp__web__echo" in lab.pi.tools
        assert await lab.text("mcp__web__echo", {"text": "over http"}) == "echo: over http"
        assert lab.pi.notices == []

    async def test_the_headers_of_the_client_reach_the_server(self, lab: Lab, remote: HttpServer):
        await lab.start(remote.client_view())

        sent = await lab.text("mcp__web__header", {"name": "Authorization"})

        assert sent == f"Bearer {TOKEN}"

    async def test_the_status_shows_the_host_and_nothing_else(self, lab: Lab, remote: HttpServer):
        manager = await lab.start(remote.client_view(url=remote.url + f"?key={TOKEN}"))

        status = await manager.status()

        assert f"- web: {remote.mode} (127.0.0.1), ready, " in status
        assert TOKEN not in status and str(remote.port) not in status

    async def test_closing_ends_the_connection_and_leaves_the_server_alone(
        self, lab: Lab, remote: HttpServer
    ):
        manager = await lab.start(remote.client_view())

        await manager.close()

        assert remote.process is not None and remote.process.poll() is None  # not ours to end
        assert manager.connections["web"].state == "closed"

    async def test_a_server_that_is_not_there_is_reported_without_its_secrets(self, lab: Lab):
        server = ClientMcpServer(
            name="web",
            transport="http",
            url=f"http://127.0.0.1:{free_port()}/mcp/{'p' * 20}?token={TOKEN}",  # nothing listens
            headers=(("Authorization", f"Bearer {TOKEN}"), ("X-Key", "key-value-98765")),
        )

        manager = await lab.start(server)

        connection = manager.connections["web"]
        assert connection.state == "failed"
        shown = " ".join([*lab.pi.notices, await manager.status(), connection.error or ""])
        for secret in (TOKEN, "key-value-98765", "p" * 20):
            assert secret not in shown
        assert 'MCP server "web" did not start:' in lab.pi.notices[0]

    async def test_a_server_that_comes_back_is_used_again(self, lab: Lab):
        remote = HttpServer("http")
        remote.start()
        try:
            manager = await lab.start(remote.client_view())
            assert await lab.text("mcp__web__echo", {"text": "one"}) == "echo: one"

            remote.stop()
            started = time.monotonic()
            with pytest.raises(RuntimeError):
                await lab.call("mcp__web__echo", {"text": "while it is down"})
            assert time.monotonic() - started < 10  # an answer, and not the end of the time limit
            remote.start()

            assert await lab.text("mcp__web__echo", {"text": "two"}) == "echo: two"
            assert manager.connections["web"].state == "ready"
        finally:
            remote.stop()


class TestLoopback:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/mcp",
            "http://localhost:3000/sse",
            "http://LOCALHOST/mcp",
            "http://[::1]:9/mcp",
            "http://127.1.2.3/mcp",
            "http://tool.localhost/mcp",
            "https://user:pw@localhost/mcp?k=v",
        ],
    )
    def test_the_addresses_of_this_machine(self, url: str):
        assert is_loopback(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/mcp",
            "http://192.0.2.5:8000/mcp",  # a documentation address (RFC 5737)
            "http://198.51.100.1/mcp",
            "http://localhost.example.com/mcp",
            "http://127.0.0.1.example.com/mcp",
            "http://[bad",
            "",
            "not a url",
        ],
    )
    def test_other_addresses(self, url: str):
        assert not is_loopback(url)

    async def test_a_server_on_this_machine_is_not_reached_through_a_proxy(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ):
        # A proxy that nothing listens on: any request that went through it would fail.
        dead_proxy = f"http://127.0.0.1:{free_port()}"
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, dead_proxy)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        remote = HttpServer("http")
        remote.start()
        try:
            await lab.start(remote.client_view())

            assert await lab.text("mcp__web__echo", {"text": "direct"}) == "echo: direct"
        finally:
            remote.stop()

    async def test_the_client_for_this_machine_ignores_proxy_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")

        async with direct_client(headers={"X-A": "1"}) as client:
            assert client.headers["X-A"] == "1"
            assert client._mounts == {}  # no proxy transport was mounted
            assert client.timeout == httpx.Timeout(30.0, read=300.0)

        async with httpx.AsyncClient() as ordinary:
            assert ordinary._mounts  # the ordinary client does take the proxy from the environment
