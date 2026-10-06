"""ACP session end: ``session/close`` and ``pi/session/delete`` close the harness (audit P7-07).

ACP: on ``session/close`` the agent cancels ongoing work as if ``session/cancel`` had been
sent, and frees the session's resources. Here ``close_session`` only ran the cleanup callbacks
(the turn in flight ran on) and ``pi/session/delete`` only aborted: a background workflow and
every other cleanup callback outlived a deleted session.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from acp import text_block

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.tests.mock_stream import _base_partial
from pi_agent_core.types import ErrorEvent, Model, StartEvent


class _Client:
    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        pass

    async def request_permission(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no permission prompt expected")


class _Holds:
    """An LLM that waits until the turn is aborted (then reports it, as a StreamFn must)."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def __call__(self, model: Model, context: Any, options: Any = None) -> Any:
        self.entered.set()
        await options.signal.wait_aborted()
        partial = _base_partial(model)
        partial.stopReason = "aborted"
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(ErrorEvent(partial=partial.model_copy(deep=True), reason="aborted"))
        stream.set_final_message(partial)
        stream.end()
        return stream


def _agent(tmp_path: Path, stream_fn: Any) -> PiAcpAgent:
    agent = PiAcpAgent(
        stream_fn=stream_fn,
        home=tmp_path,
        config=CliConfig(permission="ask", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )
    agent.on_connect(_Client())  # type: ignore[arg-type]
    return agent


async def _new_session(agent: PiAcpAgent, tmp_path: Path) -> str:
    return (await agent.new_session(cwd=str(tmp_path.resolve()))).session_id


async def test_closing_a_session_stops_the_turn_in_flight(tmp_path: Path):
    llm = _Holds()
    agent = _agent(tmp_path, llm)
    session_id = await _new_session(agent, tmp_path)
    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("go")]))
    await asyncio.wait_for(llm.entered.wait(), 5)

    await asyncio.wait_for(agent.close_session(session_id), 5)

    response = await asyncio.wait_for(turn, 5)
    assert response.stop_reason == "cancelled"


async def test_closing_a_session_runs_the_cleanup_callbacks_of_its_extensions(tmp_path: Path):
    from pi_agent_core.tests.mock_stream import mock_text_stream

    agent = _agent(tmp_path, mock_text_stream)
    session_id = await _new_session(agent, tmp_path)
    harness = agent._harnesses[session_id]
    ran: list[str] = []
    harness._create_bridge().register_cleanup(lambda: ran.append("cleaned"))

    await agent.close_session(session_id)

    assert ran == ["cleaned"]
    assert harness.closed


async def test_deleting_a_session_closes_its_harness(tmp_path: Path):
    from pi_agent_core.tests.mock_stream import mock_text_stream

    agent = _agent(tmp_path, mock_text_stream)
    session_id = await _new_session(agent, tmp_path)
    harness = agent._harnesses[session_id]
    ran: list[str] = []
    harness._create_bridge().register_cleanup(lambda: ran.append("cleaned"))

    deleted = await agent.ext_method("pi/session/delete", {"sessionId": session_id})

    assert deleted == {"sessionId": session_id, "deleted": True}
    assert ran == ["cleaned"]
    assert harness.closed


async def test_deleting_a_session_stops_the_turn_in_flight(tmp_path: Path):
    llm = _Holds()
    agent = _agent(tmp_path, llm)
    session_id = await _new_session(agent, tmp_path)
    turn = asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block("go")]))
    await asyncio.wait_for(llm.entered.wait(), 5)

    await asyncio.wait_for(agent.ext_method("pi/session/delete", {"sessionId": session_id}), 5)

    response = await asyncio.wait_for(turn, 5)
    assert response.stop_reason == "cancelled"
