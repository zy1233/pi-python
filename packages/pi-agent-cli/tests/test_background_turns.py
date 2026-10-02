"""A turn the client did not start: an extension delivers a result and the model answers.

ACP v1 has no way to tell the client that the session is running such a turn.
``state_update`` (idle / running) is a notification of the v2 draft: neither the Python SDK
nor the crate behind the TUI can parse it, and this agent never claims protocol 2, so it is
not sent. What the agent can do on its own is not to fail a ``session/prompt`` that happens
to arrive meanwhile: the prompt waits for the turn (audit row P7-07).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError, text_block
from acp.schema import SessionNotification
from pydantic import ValidationError

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.tests.mock_stream import mock_text_stream

WAIT = 10  # seconds: a broken test fails instead of hanging


class _Client:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        raise AssertionError("these tests run with permission = auto: nothing to ask")

    def said(self) -> str:
        return "".join(
            update.content.text
            for update in self.updates
            if getattr(update, "session_update", None) == "agent_message_chunk"
        )


class _HeldModel:
    """A model whose *hold*-th answer waits until the test lets it go; the others are immediate."""

    def __init__(self, hold: int = 1) -> None:
        self.hold = hold
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, model, context, options=None):
        self.calls += 1
        if self.calls == self.hold:
            self.started.set()
            await self.release.wait()
        return await mock_text_stream(model, context, options)


async def _session(tmp_path: Path, model: _HeldModel) -> tuple[PiAcpAgent, _Client, str, Any]:
    """A session, and the bridge an extension uses to start turns of its own."""
    captured: dict[str, Any] = {}

    def extension(pi):
        captured["bridge"] = pi._require_bridge()

    agent = PiAcpAgent(
        stream_fn=model.stream,
        home=tmp_path,
        config=CliConfig(permission="auto", provider="mock", model_id="mock"),
        extensions=[extension],
    )
    client = _Client()
    agent.on_connect(client)
    created = await agent.new_session(cwd=str(tmp_path.resolve()))
    return agent, client, created.session_id, captured["bridge"]


async def _turn_under_way(tmp_path: Path, model: _HeldModel):
    """A session whose model is partway through a turn an extension started."""
    agent, client, session_id, bridge = await _session(tmp_path, model)
    bridge.trigger_message("note", "a background result")
    await asyncio.wait_for(model.started.wait(), WAIT)
    return agent, client, session_id


def _ask(agent: PiAcpAgent, session_id: str, text: str) -> asyncio.Task:
    return asyncio.create_task(agent.prompt(session_id=session_id, prompt=[text_block(text)]))


@pytest.mark.asyncio
async def test_a_prompt_that_arrives_during_such_a_turn_waits_for_it(tmp_path):
    model = _HeldModel()
    agent, client, session_id = await _turn_under_way(tmp_path, model)

    prompt = _ask(agent, session_id, "my question")
    await asyncio.sleep(0.1)
    assert not prompt.done()  # it waits; it does not fail as busy
    assert model.calls == 1  # and has not reached the model

    model.release.set()
    response = await asyncio.wait_for(prompt, WAIT)

    assert response.stop_reason == "end_turn"
    assert model.calls == 2  # the background turn, then the prompt
    assert client.said().count("Hello from mock") == 2  # the client saw both answers


@pytest.mark.asyncio
async def test_cancelling_ends_a_prompt_that_is_waiting_for_such_a_turn(tmp_path):
    model = _HeldModel()
    agent, _, session_id = await _turn_under_way(tmp_path, model)
    prompt = _ask(agent, session_id, "my question")
    await asyncio.sleep(0.1)

    await agent.cancel(session_id=session_id)
    response = await asyncio.wait_for(prompt, WAIT)
    model.release.set()  # let the held (and aborted) background turn go
    await asyncio.wait_for(agent._harnesses[session_id].wait_for_idle(), WAIT)

    assert response.stop_reason == "cancelled"
    assert model.calls == 1  # the question never reached the model


@pytest.mark.asyncio
async def test_closing_the_session_ends_a_prompt_that_is_waiting_for_such_a_turn(tmp_path):
    model = _HeldModel()
    agent, _, session_id = await _turn_under_way(tmp_path, model)
    prompt = _ask(agent, session_id, "my question")
    await asyncio.sleep(0.1)

    closing = asyncio.create_task(agent.close_session(session_id=session_id))
    await asyncio.sleep(0.05)
    model.release.set()
    await asyncio.wait_for(closing, WAIT)
    response = await asyncio.wait_for(prompt, WAIT)

    assert response.stop_reason == "cancelled"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_a_second_prompt_while_one_is_in_flight_is_still_refused_as_busy(tmp_path):
    model = _HeldModel()
    agent, _, session_id, _ = await _session(tmp_path, model)
    first = _ask(agent, session_id, "one")
    await asyncio.wait_for(model.started.wait(), WAIT)

    with pytest.raises(RequestError) as refused:
        await asyncio.wait_for(
            agent.prompt(session_id=session_id, prompt=[text_block("two")]), WAIT
        )

    assert refused.value.data == {"reason": "busy"}
    model.release.set()
    assert (await asyncio.wait_for(first, WAIT)).stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_a_second_prompt_is_refused_even_while_the_first_waits_for_a_background_turn(
    tmp_path,
):
    model = _HeldModel()
    agent, _, session_id = await _turn_under_way(tmp_path, model)
    first = _ask(agent, session_id, "one")
    await asyncio.sleep(0.1)

    with pytest.raises(RequestError) as refused:
        await asyncio.wait_for(
            agent.prompt(session_id=session_id, prompt=[text_block("two")]), WAIT
        )

    assert refused.value.data == {"reason": "busy"}
    model.release.set()
    assert (await asyncio.wait_for(first, WAIT)).stop_reason == "end_turn"
    assert model.calls == 2  # the second one never ran


@pytest.mark.asyncio
async def test_a_prompt_that_has_finished_no_longer_counts_against_the_next(tmp_path):
    model = _HeldModel(hold=2)  # the first prompt is answered at once, the background turn is held
    agent, _, session_id, bridge = await _session(tmp_path, model)
    await asyncio.wait_for(agent.prompt(session_id=session_id, prompt=[text_block("one")]), WAIT)
    bridge.trigger_message("note", "a background result")
    await asyncio.wait_for(model.started.wait(), WAIT)

    second = _ask(agent, session_id, "two")
    await asyncio.sleep(0.1)
    assert not second.done()  # it waits for the background turn: it is not "a second prompt"

    model.release.set()
    assert (await asyncio.wait_for(second, WAIT)).stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_an_error_other_than_busy_is_not_waited_out(tmp_path):
    model = _HeldModel()
    model.release.set()
    agent, _, session_id, _ = await _session(tmp_path, model)
    await agent._harnesses[session_id].close()  # the harness now refuses prompts, for good

    with pytest.raises(Exception) as refused:
        await asyncio.wait_for(agent.prompt(session_id=session_id, prompt=[text_block("hi")]), WAIT)

    assert getattr(refused.value, "code", None) == "invalid_state"


@pytest.mark.asyncio
async def test_a_prompt_on_an_idle_session_runs_at_once(tmp_path):
    model = _HeldModel()
    model.release.set()  # nothing is held
    agent, client, session_id, _ = await _session(tmp_path, model)

    response = await asyncio.wait_for(
        agent.prompt(session_id=session_id, prompt=[text_block("hello")]), WAIT
    )

    assert response.stop_reason == "end_turn"
    assert model.calls == 1
    assert "Hello from mock" in client.said()
    assert agent._prompts_in_flight == {}  # nothing is left counting


# --- why ``state_update`` is not sent ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_agent_never_claims_the_v2_draft(tmp_path):
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path,
        config=CliConfig(permission="auto", provider="mock", model_id="mock"),
    )

    response = await agent.initialize(protocol_version=2)

    assert response.protocol_version == 1


def test_state_update_is_not_a_session_update_the_sdk_can_read():
    """A tripwire. If this starts failing the SDK speaks v2: look at audit row P7-07 again.

    Until then ``state_update`` would be rejected by the SDK (and by the crate the TUI is
    built on, whose ``SessionUpdate`` enum has no such variant and no catch-all).
    """
    with pytest.raises(ValidationError):
        SessionNotification.model_validate(
            {"sessionId": "s", "update": {"sessionUpdate": "state_update", "state": "idle"}}
        )
