"""What an extension gets for the MCP servers a client passes (design: docs/specs/2026-10-23-
mcp-extension-design.md).

Core holds no MCP client. The host (the CLI) gives the servers to the harness as data, and an
extension that wants them reads, claims and connects them. This file checks the data type and the
five members of ``ExtensionAPI`` that carry the idea; ``packages/pi-agent-harness`` checks what
the harness does with them.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from pi_agent_core.extensions import (
    ClientMcpServer,
    ExtensionAPI,
    ExtensionLoader,
    ExtensionMeta,
    ExtensionRegistry,
)
from pi_agent_core.extensions._harness_bridge import HarnessBridge

SECRET = "SECRET-TOKEN-1234"


def stdio_server(name: str = "files") -> ClientMcpServer:
    return ClientMcpServer(
        name=name,
        transport="stdio",
        command="npx",
        args=("-y", f"server --token {SECRET}"),
        env=(("API_KEY", SECRET),),
    )


def remote_server(name: str = "search", transport: str = "http") -> ClientMcpServer:
    return ClientMcpServer(
        name=name,
        transport=transport,  # type: ignore[arg-type]
        url=f"https://example.invalid/mcp?key={SECRET}",
        headers=(("Authorization", f"Bearer {SECRET}"),),
    )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def test_a_record_is_plain_data_that_cannot_change():
    server = stdio_server()

    with pytest.raises(dataclasses.FrozenInstanceError):
        server.name = "other"  # type: ignore[misc]
    assert {server: 1}[stdio_server()] == 1  # equal records hash alike


def test_a_record_that_is_not_given_a_field_has_an_empty_one():
    bare = ClientMcpServer(name="x", transport="stdio")

    assert (bare.command, bare.args, bare.env) == ("", (), ())
    assert (bare.url, bare.headers) == ("", ())


@pytest.mark.parametrize("server", [stdio_server(), remote_server(), remote_server("e", "sse")])
def test_the_repr_shows_no_secret(server: ClientMcpServer):
    # ``args``, ``env``, ``url`` and ``headers`` routinely hold tokens, and a record ends up in
    # tracebacks, logs and the output of a failing test.
    for text in (repr(server), str(server), f"{server}", repr([server]), repr({"s": server})):
        assert SECRET not in text
        assert server.name in text
        assert server.transport in text


# ---------------------------------------------------------------------------
# ExtensionAPI
# ---------------------------------------------------------------------------


class RecordingBridge:
    """The four members that are about the client's servers and the user."""

    def __init__(self, servers: tuple[ClientMcpServer, ...] = ()) -> None:
        self._servers = servers
        self.claimed: list[list[str]] = []
        self.notices: list[str] = []
        self.barriers: list[Any] = []
        self.cleanups: list[Any] = []

    @property
    def client_mcp_servers(self) -> tuple[ClientMcpServer, ...]:
        return self._servers

    def claim_client_mcp_servers(self, names: Any) -> None:
        self.claimed.append(list(names))

    def notify(self, text: str) -> None:
        self.notices.append(text)

    def add_turn_barrier(self, wait: Any) -> None:
        self.barriers.append(wait)

    def register_cleanup(self, callback: Any) -> None:
        self.cleanups.append(callback)


def make_api(bridge: RecordingBridge | None = None) -> ExtensionAPI:
    api = ExtensionAPI(registry=ExtensionRegistry(), meta=ExtensionMeta(name="probe"))
    if bridge is not None:
        api._set_bridge(bridge)  # type: ignore[arg-type]
    return api


@pytest.mark.parametrize(
    "member", ["client_mcp_servers", "claim_client_mcp_servers", "notify", "add_turn_barrier"]
)
def test_the_api_and_the_bridge_protocol_both_have_the_member(member: str):
    # The API calls what the bridge protocol declares, and the harness is the one bridge. A
    # member on one side only would fail at the first call of an extension, not here.
    assert hasattr(ExtensionAPI, member)
    assert hasattr(HarnessBridge, member)


def test_an_extension_reads_the_servers_the_host_passed():
    servers = (stdio_server("a"), remote_server("b"))
    api = make_api(RecordingBridge(servers))

    assert api.client_mcp_servers == servers
    assert isinstance(api.client_mcp_servers, tuple)


def test_no_servers_is_an_empty_tuple():
    assert make_api(RecordingBridge()).client_mcp_servers == ()


def test_claiming_hands_the_names_to_the_host_as_a_list():
    bridge = RecordingBridge()
    api = make_api(bridge)

    api.claim_client_mcp_servers(("a", "b"))
    api.claim_client_mcp_servers(name for name in ["c"])  # any iterable, even a generator

    assert bridge.claimed == [["a", "b"], ["c"]]


def test_notify_and_the_barrier_and_the_cleanup_reach_the_host():
    bridge = RecordingBridge()
    api = make_api(bridge)

    async def wait() -> None:
        return None

    def cleanup() -> None:
        return None

    api.notify("MCP server files failed to start")
    api.add_turn_barrier(wait)
    api.register_cleanup(cleanup)

    assert bridge.notices == ["MCP server files failed to start"]
    assert bridge.barriers == [wait]
    assert bridge.cleanups == [cleanup]


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.client_mcp_servers,
        lambda api: api.claim_client_mcp_servers(["a"]),
        lambda api: api.notify("hello"),
        lambda api: api.add_turn_barrier(lambda: None),
    ],
    ids=["client_mcp_servers", "claim_client_mcp_servers", "notify", "add_turn_barrier"],
)
def test_without_a_harness_each_member_says_so(call: Any):
    with pytest.raises(RuntimeError, match="not connected"):
        call(make_api())


def test_the_servers_are_there_while_the_extension_activates():
    # The harness hands them over before ``activate``, because an extension decides at that
    # moment what to claim and what to start.
    seen: dict[str, Any] = {}
    servers = (stdio_server("a"),)

    def extension(pi: ExtensionAPI) -> None:
        seen["servers"] = pi.client_mcp_servers
        pi.claim_client_mcp_servers([server.name for server in pi.client_mcp_servers])

    bridge = RecordingBridge(servers)
    ExtensionLoader().load_callable(extension, name="probe", bridge=bridge)  # type: ignore[arg-type]

    assert seen["servers"] == servers
    assert bridge.claimed == [["a"]]
