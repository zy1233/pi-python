"""ACP contract over real stdio: the wire-level behaviour a ``PI_AGENT_COMMAND`` must satisfy.

The in-process tests in ``test_acp_agent.py`` call ``PiAcpAgent`` directly; these drive an agent
*process* the way the Rust TUI does (JSON-RPC framing, the ``run_agent`` loop, process exit) and
assert only what the pager depends on, so they are the acceptance suite for any other agent.

By default the agent under test is ``python -m pi_agent_cli`` with the mock LLM
(``PI_USE_MOCK=1``) and a throw-away ``PI_HOME``. Set ``PI_ACP_CONTRACT_COMMAND`` to run the same
assertions against another stdio ACP agent (for example a future ``pi-rust``); that agent must
honour ``PI_HOME`` and answer prompts with the fixed text ``Hello from mock`` without network.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, RequestError, spawn_agent_process, text_block
from acp.client.connection import ClientSideConnection
from acp.schema import DeniedOutcome, RequestPermissionResponse

pytestmark = pytest.mark.asyncio

WIRE_TIMEOUT = 60.0
MOCK_REPLY = "Hello from mock"


def _agent_command() -> list[str]:
    override = os.environ.get("PI_ACP_CONTRACT_COMMAND")
    return shlex.split(override) if override else [sys.executable, "-m", "pi_agent_cli"]


class RecordingClient:
    """Minimal ACP client: records ``session/update`` and refuses every permission request."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, Any]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append((session_id, update))

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def agent_text(self, session_id: str) -> str:
        return "".join(
            getattr(getattr(update, "content", None), "text", "") or ""
            for sid, update in self.updates
            if sid == session_id
            and getattr(update, "session_update", None) == "agent_message_chunk"
        )

    def user_text(self, session_id: str) -> str:
        return "".join(
            getattr(getattr(update, "content", None), "text", "") or ""
            for sid, update in self.updates
            if sid == session_id and getattr(update, "session_update", None) == "user_message_chunk"
        )


@asynccontextmanager
async def running_agent(
    home: Path,
) -> AsyncIterator[tuple[ClientSideConnection, RecordingClient, asyncio.subprocess.Process]]:
    """Spawn the agent under test, speaking ACP over its stdio."""
    client = RecordingClient()
    env = {"PI_HOME": str(home), "PI_USE_MOCK": "1"}
    command, *args = _agent_command()
    async with spawn_agent_process(
        client,
        command,
        *args,
        env=env,
        cwd=home,
        transport_kwargs={"stderr": subprocess.DEVNULL, "shutdown_timeout": 10.0},
    ) as (conn, process):
        yield conn, client, process


async def _wire(awaitable: Any) -> Any:
    return await asyncio.wait_for(awaitable, WIRE_TIMEOUT)


async def test_initialize_advertises_standard_sessions_and_no_auth(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, _process):
        init = await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
    assert init.protocol_version >= 1
    assert init.auth_methods == []
    capabilities = init.agent_capabilities
    assert capabilities.load_session is True
    assert capabilities.session_capabilities.list is not None


async def test_prompt_streams_message_chunks_then_ends_the_turn(tmp_path):
    async with running_agent(tmp_path) as (conn, client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        session = await _wire(conn.new_session(cwd=str(tmp_path), mcp_servers=[]))
        assert session.session_id
        result = await _wire(
            conn.prompt(session_id=session.session_id, prompt=[text_block("say hello")])
        )
    assert result.stop_reason == "end_turn"
    assert client.agent_text(session.session_id) == MOCK_REPLY


async def test_model_option_is_a_session_config_select(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        session = await _wire(conn.new_session(cwd=str(tmp_path), mcp_servers=[]))
    (model,) = [option for option in session.config_options or [] if option.id == "model"]
    assert model.category == "model"
    assert model.type == "select"
    assert model.current_value in {choice.value for choice in model.options}


async def test_sessions_survive_a_restart_listed_with_a_title_and_replayed_on_load(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        created = await _wire(conn.new_session(cwd=str(tmp_path), mcp_servers=[]))
        await _wire(
            conn.prompt(session_id=created.session_id, prompt=[text_block("remember  this")])
        )

    async with running_agent(tmp_path) as (conn, client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        listed = await _wire(conn.list_sessions(cwd=str(tmp_path)))
        (info,) = [s for s in listed.sessions if s.session_id == created.session_id]
        assert info.title == "remember this"
        assert info.updated_at

        loaded = await _wire(
            conn.load_session(cwd=str(tmp_path), session_id=created.session_id, mcp_servers=[])
        )
        assert loaded is not None
        # History comes back as session/update notifications before the load response.
        assert client.user_text(created.session_id) == "remember  this"
        assert client.agent_text(created.session_id) == MOCK_REPLY

        again = await _wire(
            conn.prompt(session_id=created.session_id, prompt=[text_block("and again")])
        )
        assert again.stop_reason == "end_turn"


async def test_unknown_requests_are_errors_and_the_connection_survives(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        with pytest.raises(RequestError):
            await _wire(conn.ext_method("x.ai/auth/get_url", {}))
        with pytest.raises(RequestError):
            await _wire(conn.load_session(cwd=str(tmp_path), session_id="no-such-session"))
        with pytest.raises(RequestError):
            await _wire(conn.prompt(session_id="no-such-session", prompt=[text_block("hi")]))
        listed = await _wire(conn.list_sessions())
    assert listed.sessions == []


async def test_resume_reattaches_a_session_without_replaying_history(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        created = await _wire(conn.new_session(cwd=str(tmp_path), mcp_servers=[]))
        await _wire(conn.prompt(session_id=created.session_id, prompt=[text_block("first")]))

    async with running_agent(tmp_path) as (conn, client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        resumed = await _wire(conn.resume_session(cwd=str(tmp_path), session_id=created.session_id))
        assert resumed is not None
        assert client.user_text(created.session_id) == ""
        assert client.agent_text(created.session_id) == ""
        again = await _wire(
            conn.prompt(session_id=created.session_id, prompt=[text_block("second")])
        )
    assert again.stop_reason == "end_turn"


async def test_a_closed_session_rejects_further_prompts(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, _process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        session = await _wire(conn.new_session(cwd=str(tmp_path), mcp_servers=[]))
        await _wire(conn.close_session(session_id=session.session_id))
        with pytest.raises(RequestError):
            await _wire(conn.prompt(session_id=session.session_id, prompt=[text_block("hi")]))


async def test_the_agent_exits_zero_when_stdin_closes(tmp_path):
    async with running_agent(tmp_path) as (conn, _client, process):
        await _wire(conn.initialize(protocol_version=PROTOCOL_VERSION))
        await _wire(conn.new_session(cwd=str(tmp_path), mcp_servers=[]))
    # Leaving the context closes stdin and waits for a voluntary exit before escalating to
    # SIGTERM / SIGKILL, so a clean 0 here means the agent shut itself down on EOF.
    assert process.returncode == 0
