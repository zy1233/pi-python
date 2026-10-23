"""The whole way: an ACP client passes a server in ``session/new``, the agent loads ``pi-mcp``, a
(scripted) model calls its tools, the permission check of the CLI applies, ``session/close`` ends
the child process."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pi_mcp
import pytest
from acp import text_block
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    EnvVariable,
    HttpHeader,
    HttpMcpServer,
    McpServerStdio,
    RequestPermissionResponse,
)
from mcp_support import MOCK_SERVER, ToolScript, block_text, read_pid, until, wait_gone

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.extensions import ExtensionLoader

WAIT = 30
SECRET = "sk-secret-token-4711"


class Client:
    """An ACP client: it records what it is sent and answers permission requests."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.updates: list[Any] = []
        self.permission_titles: list[str] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        self.permission_titles.append(str(tool_call.title))
        if self.allow:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def agent_messages(self) -> list[str]:
        return [
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.project = tmp_path / "project"
        self.project.mkdir()
        self.agents: list[PiAcpAgent] = []

    def agent(self, llm: Any, client: Client, *, permission: str = "auto") -> PiAcpAgent:
        agent = PiAcpAgent(
            stream_fn=llm,
            home=self.tmp_path / "pi-home",
            config=CliConfig(permission=permission, provider="mock", model_id="mock"),  # type: ignore[arg-type]
            extensions=[pi_mcp.activate],
        )
        agent.on_connect(client)
        self.agents.append(agent)
        return agent

    async def aclose(self) -> None:
        for agent in self.agents:
            for session_id in list(agent._harnesses):
                await agent.close_session(session_id=session_id)


@pytest.fixture()
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    # The entry points of this environment (the real ``pi-mcp``, if installed) would load the
    # extension a second time: the tests name it themselves.
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])
    monkeypatch.setattr("pi_agent_cli.agent.mcp_extension_installed", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    laboratory = World(tmp_path)
    try:
        yield laboratory
    finally:
        await laboratory.aclose()


def mock_server(name: str = "mock", *, env: dict[str, str] | None = None) -> McpServerStdio:
    return McpServerStdio(
        name=name,
        command=sys.executable,
        args=[str(MOCK_SERVER)],
        env=[EnvVariable(name=k, value=v) for k, v in (env or {}).items()],
    )


async def settle(agent: PiAcpAgent) -> None:
    await asyncio.gather(*agent._background_tasks)


async def tool_results(agent: PiAcpAgent, session_id: str) -> list[str]:
    context = await agent._harnesses[session_id].session.build_context()
    return [
        "".join(block_text(b) for b in message.content)
        for message in context.messages
        if getattr(message, "role", None) == "toolResult"
    ]


async def open_session(
    world: World, llm: Any, servers: list[Any], *, permission: str = "auto", allow: bool = True
) -> tuple[PiAcpAgent, Client, str]:
    client = Client(allow=allow)
    agent = world.agent(llm, client, permission=permission)
    created = await agent.new_session(cwd=str(world.project), mcp_servers=servers)
    await settle(agent)
    return agent, client, created.session_id


async def prompt(agent: PiAcpAgent, session_id: str, text: str = "go") -> Any:
    return await asyncio.wait_for(
        agent.prompt(session_id=session_id, prompt=[text_block(text)]), WAIT
    )


class TestThroughTheAgent:
    async def test_a_server_of_the_client_gives_the_model_its_tools(self, world: World):
        llm = ToolScript(("mcp__mock__echo", {"text": "via acp"}))
        agent, client, sid = await open_session(world, llm, [mock_server()])

        await prompt(agent, sid)

        assert await tool_results(agent, sid) == ["echo: via acp"]
        assert "mcp__mock__echo" in llm.offered[0]
        assert [m for m in client.agent_messages() if "MCP" in m] == []  # nothing is "ignored"

    async def test_the_client_sees_the_call_as_a_tool_call(self, world: World):
        llm = ToolScript(("mcp__mock__echo", {"text": "x"}))
        agent, client, sid = await open_session(world, llm, [mock_server()])

        await prompt(agent, sid)

        kinds = [getattr(u, "session_update", None) for u in client.updates]
        assert "tool_call" in kinds and "tool_call_update" in kinds
        titles = [getattr(u, "title", None) for u in client.updates if getattr(u, "title", None)]
        assert any("mock: echo" in t or "mcp__mock__echo" in t for t in titles)

    async def test_ask_mode_asks_before_a_tool_that_does_not_say_it_is_harmless(self, world: World):
        llm = ToolScript(("mcp__mock__echo", {"text": "ask me"}))
        agent, client, sid = await open_session(world, llm, [mock_server()], permission="ask")

        await prompt(agent, sid)

        assert len(client.permission_titles) == 1
        assert await tool_results(agent, sid) == ["echo: ask me"]

    async def test_ask_mode_does_not_ask_for_a_tool_that_says_it_only_reads(self, world: World):
        llm = ToolScript(("mcp__mock__readonly", {}))
        agent, client, sid = await open_session(world, llm, [mock_server()], permission="ask")

        await prompt(agent, sid)

        assert client.permission_titles == []
        assert await tool_results(agent, sid) == ["read"]

    async def test_a_refused_call_does_not_reach_the_server(self, world: World):
        llm = ToolScript(("mcp__mock__echo", {"text": "no"}))
        agent, client, sid = await open_session(
            world, llm, [mock_server()], permission="ask", allow=False
        )

        await prompt(agent, sid)

        assert len(client.permission_titles) == 1
        results = await tool_results(agent, sid)
        assert len(results) == 1 and "echo: no" not in results[0]

    async def test_session_close_ends_the_server_process(self, world: World):
        pid_file = world.tmp_path / "s.pid"
        llm = ToolScript()
        agent, _, sid = await open_session(
            world, llm, [mock_server(env={"PI_MCP_TEST_PID_FILE": str(pid_file)})]
        )
        await prompt(agent, sid)
        pid = await read_pid(pid_file)

        await agent.close_session(session_id=sid)

        await wait_gone(pid)

    async def test_a_server_that_fails_to_start_is_reported_to_the_client(self, world: World):
        llm = ToolScript()
        ghost = McpServerStdio(name="ghost", command="no-such-command-pi-mcp", args=[], env=[])
        agent, client, sid = await open_session(world, llm, [ghost])

        await prompt(agent, sid)

        await until(lambda: any("did not start" in m for m in client.agent_messages()))
        [message] = [m for m in client.agent_messages() if "ghost" in m]
        assert message.startswith('MCP server "ghost" did not start: command not found')

    async def test_a_remote_server_with_a_token_never_shows_it(self, world: World):
        llm = ToolScript()
        remote = HttpMcpServer(
            type="http",
            name="remote",
            url=f"http://127.0.0.1:9/mcp?key={SECRET}",  # nothing listens on port 9
            headers=[HttpHeader(name="Authorization", value=f"Bearer {SECRET}")],
        )
        agent, client, sid = await open_session(world, llm, [remote])

        await prompt(agent, sid)
        await until(lambda: any('"remote" did not start' in m for m in client.agent_messages()))

        everything = " ".join(client.agent_messages())
        assert SECRET not in everything

    async def test_the_command_works_through_the_prompt(self, world: World):
        llm = ToolScript()
        agent, client, sid = await open_session(world, llm, [mock_server()])
        await prompt(agent, sid)  # the barrier: the server is ready

        await prompt(agent, sid, "/mcp")

        text = " ".join(client.agent_messages())
        assert "MCP servers (1):" in text and "- mock: stdio (" in text
