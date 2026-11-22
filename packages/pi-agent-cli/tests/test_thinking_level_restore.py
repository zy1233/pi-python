"""``session/load`` and ``session/resume`` start a chat at the thinking level saved in it."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from acp import text_block

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.tests.mock_stream import mock_text_stream


class _Client:
    async def session_update(self, session_id, update, **kwargs):
        return None


def _agent(tmp_path: Path, **config: Any) -> PiAcpAgent:
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path,
        config=CliConfig(provider="mock", model_id="mock", **config),
    )
    agent.on_connect(_Client())
    return agent


def _level_option(options):
    found = [option for option in options if option.id == "thinking_level"]
    return found[0] if found else None


async def _saved_chat(tmp_path: Path, *levels: str) -> tuple[str, str]:
    """A saved chat in which the user set *levels* one after another, then prompted."""
    agent = _agent(tmp_path)
    cwd = str(tmp_path.resolve())
    sid = (await agent.new_session(cwd=cwd)).session_id
    for level in levels:
        await agent.set_config_option(config_id="thinking_level", session_id=sid, value=level)
    await agent.prompt(session_id=sid, prompt=[text_block("hi")])
    return sid, cwd


def _session_files(home: Path) -> list[Path]:
    return sorted(home.rglob("*.jsonl"))


@pytest.mark.asyncio
async def test_a_loaded_chat_starts_at_the_level_saved_in_it(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high")

    other = _agent(tmp_path)
    loaded = await other.load_session(cwd=cwd, session_id=sid)

    assert loaded is not None
    assert _level_option(loaded.config_options).current_value == "high"
    harness = other._harnesses[sid]
    assert harness.thinking_level == "high"
    # Invariant 5: the gate that injects reasoning follows the level, as after a live change.
    assert harness.model.reasoning is True


@pytest.mark.asyncio
async def test_the_last_saved_level_wins(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high", "low")

    other = _agent(tmp_path)
    loaded = await other.load_session(cwd=cwd, session_id=sid)

    assert _level_option(loaded.config_options).current_value == "low"


@pytest.mark.asyncio
async def test_a_saved_off_beats_a_level_in_agent_toml(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high", "off")

    other = _agent(tmp_path, thinking_level="medium")
    loaded = await other.load_session(cwd=cwd, session_id=sid)

    assert _level_option(loaded.config_options).current_value == "off"
    assert other._harnesses[sid].thinking_level == "off"
    assert other._harnesses[sid].model.reasoning is False


@pytest.mark.asyncio
async def test_a_chat_with_no_saved_level_keeps_the_level_of_agent_toml(tmp_path):
    sid, cwd = await _saved_chat(tmp_path)

    other = _agent(tmp_path, thinking_level="medium")
    loaded = await other.load_session(cwd=cwd, session_id=sid)

    assert _level_option(loaded.config_options).current_value == "medium"
    assert other._harnesses[sid].thinking_level == "medium"
    assert other._harnesses[sid].model.reasoning is True


@pytest.mark.asyncio
async def test_a_new_chat_starts_at_the_level_of_agent_toml(tmp_path):
    await _saved_chat(tmp_path, "high")

    other = _agent(tmp_path, thinking_level="low")
    created = await other.new_session(cwd=str(tmp_path.resolve()))

    assert _level_option(created.config_options).current_value == "low"
    assert other._harnesses[created.session_id].thinking_level == "low"


@pytest.mark.asyncio
async def test_a_saved_level_is_not_used_when_the_model_cannot_think(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high")

    other = _agent(tmp_path, reasoning=False)
    loaded = await other.load_session(cwd=cwd, session_id=sid)

    assert loaded is not None
    assert _level_option(loaded.config_options) is None
    harness = other._harnesses[sid]
    assert harness.thinking_level == "off"
    assert harness.model.reasoning is False


@pytest.mark.asyncio
async def test_a_saved_level_that_the_option_does_not_offer_is_not_used(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high")
    writer = _agent(tmp_path)
    await writer.load_session(cwd=cwd, session_id=sid)
    await writer._harnesses[sid].session.append_thinking_level_change("turbo")

    other = _agent(tmp_path, thinking_level="low")
    loaded = await other.load_session(cwd=cwd, session_id=sid)

    assert _level_option(loaded.config_options).current_value == "low"
    assert other._harnesses[sid].thinking_level == "low"


@pytest.mark.asyncio
async def test_loading_writes_nothing_to_the_chat_file(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high")
    (path,) = _session_files(tmp_path)
    os.utime(path, (1_000_000_000, 1_000_000_000))
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    other = _agent(tmp_path)
    listed_before = [s.session_id for s in (await other.list_sessions(cwd=cwd)).sessions]
    await other.load_session(cwd=cwd, session_id=sid)
    await other.resume_session(session_id=sid, cwd=cwd)
    listed_after = [s.session_id for s in (await other.list_sessions(cwd=cwd)).sessions]

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert listed_after == listed_before
    assert other._harnesses[sid].thinking_level == "high"


@pytest.mark.asyncio
async def test_a_resumed_chat_starts_at_the_level_saved_in_it(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "xhigh")

    other = _agent(tmp_path)
    resumed = await other.resume_session(session_id=sid, cwd=cwd)

    assert _level_option(resumed.config_options).current_value == "xhigh"
    harness = other._harnesses[sid]
    assert harness.thinking_level == "xhigh"
    assert harness.model.reasoning is True


@pytest.mark.asyncio
async def test_a_level_set_after_a_restored_one_is_saved_as_before(tmp_path):
    sid, cwd = await _saved_chat(tmp_path, "high")
    other = _agent(tmp_path)
    await other.load_session(cwd=cwd, session_id=sid)
    await other.set_config_option(config_id="thinking_level", session_id=sid, value="low")

    third = _agent(tmp_path)
    loaded = await third.load_session(cwd=cwd, session_id=sid)

    assert _level_option(loaded.config_options).current_value == "low"
