"""The MCP servers a client passes are not used, and the user is told (plan 1.P4).

ACP lets a client hand an agent MCP servers with ``session/new`` / ``load`` / ``resume``. This
agent connects to none; saying nothing would leave the user wondering where the tools of the
server they set up in their editor went.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp.schema import EnvVariable, HttpHeader, HttpMcpServer, McpServerStdio, SseMcpServer

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import TRUST_ENV
from pi_agent_cli.mcp_notice import ignored_mcp_servers_notice, mcp_server_names
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream

SECRET = "SECRET-TOKEN-1234"


def stdio(name: str = "files") -> McpServerStdio:
    return McpServerStdio(
        name=name,
        command="npx",
        args=["-y", f"server --token {SECRET}"],
        env=[EnvVariable(name="API_KEY", value=SECRET)],
    )


def http(name: str = "search") -> HttpMcpServer:
    return HttpMcpServer(
        type="http",
        name=name,
        url=f"https://example.invalid/mcp?key={SECRET}",
        headers=[HttpHeader(name="Authorization", value=f"Bearer {SECRET}")],
    )


def sse(name: str = "events") -> SseMcpServer:
    return SseMcpServer(type="sse", name=name, url="https://example.invalid/sse", headers=[])


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


def test_names_come_from_every_kind_of_server_in_order():
    assert mcp_server_names([stdio(), http(), sse()]) == ["files", "search", "events"]
    assert mcp_server_names([{"name": "from a dict"}]) == ["from a dict"]
    assert mcp_server_names(None) == [] == mcp_server_names([])


def test_a_server_without_a_usable_name_is_still_counted():
    nameless = SimpleNamespace(name="  ")
    assert mcp_server_names([nameless, object(), {"name": 3}]) == ["(unnamed)"] * 3


def test_names_are_one_short_line():
    [name] = mcp_server_names([stdio("two\nlines  and\ttabs")])
    assert name == "two lines and tabs"
    [long] = mcp_server_names([stdio("n" * 500)])
    assert len(long) <= 60 and long.endswith("...")


def test_there_is_no_notice_without_servers():
    assert ignored_mcp_servers_notice([]) is None


def test_the_notice_counts_names_and_says_the_tools_are_missing():
    one = ignored_mcp_servers_notice(["files"])
    two = ignored_mcp_servers_notice(["files", "search"])
    assert one is not None and "1 MCP server " in one and "(files)" in one
    assert two is not None and "2 MCP servers " in two and "(files, search)" in two
    assert "not available" in two


def test_a_long_list_is_cut_to_a_count():
    notice = ignored_mcp_servers_notice([f"s{i}" for i in range(20)])
    assert notice is not None
    assert "s0, s1" in notice and "s7" in notice and "s8" not in notice
    assert "and 12 more" in notice and "20 MCP servers" in notice


def test_the_notice_carries_no_secret():
    names = mcp_server_names([stdio(), http(), sse()])
    notice = ignored_mcp_servers_notice(names)
    assert notice is not None and SECRET not in notice


# ---------------------------------------------------------------------------
# Delivered to the user
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    project = tmp_path / "project"
    project.mkdir()
    return SimpleNamespace(pi_home=home / ".pi-python", project=project)


class _RecordingClient:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        raise AssertionError("no tool should ask for permission here")

    def agent_messages(self) -> list[str]:
        return [
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]


def make_agent(world: Any) -> tuple[PiAcpAgent, _RecordingClient]:
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=world.pi_home,
        config=CliConfig(permission="auto", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )
    client = _RecordingClient()
    agent.on_connect(client)
    return agent, client


async def settle(agent: PiAcpAgent) -> None:
    # Notices go out after the response has flushed (Zed drops earlier ones).
    await asyncio.gather(*agent._background_tasks)


async def test_new_session_says_which_servers_were_ignored(world: Any):
    agent, client = make_agent(world)
    await agent.new_session(cwd=str(world.project), mcp_servers=[stdio(), http(), sse()])
    await settle(agent)

    (message,) = client.agent_messages()
    assert "3 MCP servers" in message and "files, search, events" in message


async def test_load_and_resume_say_it_too(world: Any):
    agent, client = make_agent(world)
    created = await agent.new_session(cwd=str(world.project))
    await settle(agent)
    await agent.close_session(session_id=created.session_id)
    assert client.agent_messages() == []

    await agent.load_session(
        cwd=str(world.project), session_id=created.session_id, mcp_servers=[stdio("loaded")]
    )
    await settle(agent)
    await agent.close_session(session_id=created.session_id)
    await agent.resume_session(
        cwd=str(world.project), session_id=created.session_id, mcp_servers=[http("resumed")]
    )
    await settle(agent)

    load_notice, resume_notice = client.agent_messages()
    assert "(loaded)" in load_notice
    assert "(resumed)" in resume_notice


async def test_no_servers_no_notice(world: Any):
    agent, client = make_agent(world)
    await agent.new_session(cwd=str(world.project), mcp_servers=[])
    await agent.new_session(cwd=str(world.project))
    await settle(agent)
    assert client.agent_messages() == []


async def test_the_log_names_the_servers_and_no_secret(
    world: Any, caplog: pytest.LogCaptureFixture
):
    agent, _ = make_agent(world)
    with caplog.at_level(logging.WARNING, logger="pi_agent_cli.agent"):
        await agent.new_session(cwd=str(world.project), mcp_servers=[stdio(), http()])
        await settle(agent)
    text = caplog.text
    assert "Ignoring 2 MCP server(s)" in text and "files, search" in text
    assert SECRET not in text


async def test_the_session_still_works_with_servers_passed(world: Any):
    from acp import text_block

    agent, client = make_agent(world)
    created = await agent.new_session(cwd=str(world.project), mcp_servers=[stdio()])
    await settle(agent)
    result = await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    assert result.stop_reason == "end_turn"
    assert any("Hello from mock" in m for m in client.agent_messages())
