"""The gateway against real agents, over ACP stdio, with a fake chat platform.

``python -m pi_agent_cli`` answers every prompt with the mock LLM ("Hello from mock").
``_tool_agent.py`` calls one tool in its first turn (``PI_TEST_TOOL_CALL``), so the permission
path runs for real: the agent asks, the gateway posts a card, a tap answers.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from gateway_support import (
    OWNER,
    SECRET,
    TENANT,
    T,
    finished_turn,
    make_config,
    notices_text,
    running_gateway,
)

from conftest import mock_agent_command, tool_agent_command
from pi_chat_gateway.identity import Pairing
from pi_chat_gateway.platforms.base import InboundMessage
from pi_chat_gateway.platforms.fake import FakePlatform

MOCK = {"PI_USE_MOCK": "1"}


def _write_call(path: str, content: str) -> dict[str, str]:
    call = {"name": "write", "arguments": {"path": path, "content": content}}
    return {"PI_TEST_TOOL_CALL": json.dumps(call), "PI_TEST_PERMISSION": "ask"}


def _approval(platform: FakePlatform):
    """The latest approval card: a message that had buttons in any of its versions."""
    cards = [s for s in platform.sent if any(card.buttons for card in s.cards)]
    return cards[-1] if cards else None


async def test_a_message_runs_a_turn_and_the_card_shows_the_answer(home: Path, project: Path):
    config = make_config(home, project)
    async with running_gateway(config, command=mock_agent_command(), agent_env=MOCK) as (
        _,
        platform,
    ):
        await platform.say("hello")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        turn = finished_turn(platform)
        assert turn.card.title.endswith(T("status_done"))
        assert "Hello from mock" in turn.text
        # The first version of the card went out before the answer, then it was updated.
        assert len(turn.cards) >= 2


async def test_allow_once_runs_the_tool(home: Path, project: Path):
    config = make_config(home, project)
    env = _write_call("allowed.txt", "from the chat")
    async with running_gateway(config, command=tool_agent_command(), agent_env=env) as (
        _,
        platform,
    ):
        await platform.say("write the file")
        await platform.wait_for(lambda: _approval(platform) is not None, 120)
        card = _approval(platform)
        assert "allowed.txt" in card.text and "from the chat" in card.text
        reply = await platform.tap(card.message_id, T("btn_allow_once"))
        assert reply.level == "success" and reply.card is not None and not reply.card.buttons
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
    assert (project / "allowed.txt").read_text(encoding="utf-8") == "from the chat"
    log = (home / "chat" / "audit.log").read_text(encoding="utf-8")
    assert '"event":"approval"' in log and '"outcome":"allowed"' in log


async def test_reject_and_a_strangers_tap(home: Path, project: Path):
    config = make_config(home, project)
    env = _write_call("rejected.txt", "no")
    async with running_gateway(config, command=tool_agent_command(), agent_env=env) as (
        _,
        platform,
    ):
        await platform.say("write the file")
        await platform.wait_for(lambda: _approval(platform) is not None, 120)
        card = _approval(platform)
        stranger = await platform.tap(card.message_id, T("btn_allow_once"), user="ou_stranger")
        assert stranger.level == "error" and stranger.toast == T("approval_forbidden")
        await platform.tap(card.message_id, T("btn_reject"))
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        # A second tap on the closed request changes nothing.
        late = await platform.tap(card.message_id, T("btn_allow_once"))
        assert late.toast == T("approval_gone")
    assert not (project / "rejected.txt").exists()
    assert '"event":"approval_forbidden"' in (home / "chat" / "audit.log").read_text("utf-8")


async def test_an_unanswered_approval_is_rejected_in_time(home: Path, project: Path):
    config = replace(make_config(home, project), approval_timeout=1.0)
    env = _write_call("late.txt", "no")
    async with running_gateway(config, command=tool_agent_command(), agent_env=env) as (
        _,
        platform,
    ):
        await platform.say("write the file")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        card = platform.find(_approval(platform).message_id)
        assert T("approval_timeout") in card.text and not card.card.buttons
    assert not (project / "late.txt").exists()


async def test_the_trust_question_is_answered_no(home: Path, project: Path):
    extensions = project / ".pi-python" / "extensions"
    extensions.mkdir(parents=True)
    marker = project / "extension-ran"
    (extensions / "probe.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "def activate(pi):\n    pass\n",
        encoding="utf-8",
    )
    config = make_config(home, project)
    async with running_gateway(config, command=mock_agent_command(), agent_env=MOCK) as (
        _,
        platform,
    ):
        await platform.say("hello")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        await platform.wait_for(lambda: T("trust_rejected") in notices_text(platform), 30)
        assert not platform.with_buttons(), "the trust question is never a button"
        assert "Hello from mock" in finished_turn(platform).text
    assert not marker.exists(), "the extension of an untrusted project must not run"
    trust_file = home / "agent" / "trust.json"
    assert not trust_file.exists() or "trust-project" not in trust_file.read_text("utf-8")
    assert '"event":"trust_refused"' in (home / "chat" / "audit.log").read_text("utf-8")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/<pid>/environ")
async def test_no_credential_is_in_the_agents_environment(home: Path, project: Path):
    config = make_config(home, project)
    base = {**os.environ, "COPIED_SECRET": SECRET, "ZYCODE_CHAT_DEBUG": "1"}
    async with running_gateway(
        config, command=mock_agent_command(), agent_env=MOCK, base_env=base
    ) as (gateway, platform):
        await platform.say("hello")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        agent = gateway.pool.running("demo")
        environ = Path(f"/proc/{agent.process.pid}/environ").read_bytes()
    assert SECRET.encode() not in environ
    assert b"ZYCODE_CHAT_DEBUG" not in environ
    assert f"PI_HOME={home}".encode() in environ


async def test_steer_while_a_tool_runs_then_stop(home: Path, project: Path, tmp_path: Path):
    pidfile = tmp_path / "tool.pid"
    env = {"PI_TEST_PERMISSION": "ask", "PI_TEST_TOOL_PIDFILE": str(pidfile)}
    config = make_config(home, project)
    async with running_gateway(config, command=tool_agent_command(), agent_env=env) as (
        _,
        platform,
    ):
        await platform.say("run the long command")
        await platform.wait_for(lambda: _approval(platform) is not None, 120)
        await platform.tap(_approval(platform).message_id, T("btn_allow_once"))
        await platform.wait_for(lambda: pidfile.exists(), 60)
        steer_id = await platform.say("and then also this")
        await platform.wait_for(lambda: (steer_id, "queued") in platform.reactions, 30)
        await platform.say("/stop")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        assert finished_turn(platform).card.title.endswith(T("status_cancelled"))
        # The steer never reached the model: the chat is told, the message is marked.
        await platform.wait_for(lambda: (steer_id, "failed") in platform.reactions, 30)
        assert "and then also this" in notices_text(platform)


async def test_btw_answers_beside_the_session(home: Path, project: Path):
    config = make_config(home, project)
    async with running_gateway(config, command=mock_agent_command(), agent_env=MOCK) as (
        _,
        platform,
    ):
        await platform.say("hello")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        sessions = list((home / "sessions").glob("*.jsonl"))
        before = sessions[0].read_bytes()
        await platform.say("/btw what did you just say?")

        def btw_done() -> bool:
            return any(
                s.card.title == T("btw_title") and "Hello from mock" in s.text
                for s in platform.sent
            )

        await platform.wait_for(btw_done, 120)
        assert sessions[0].read_bytes() == before, "/btw is never written to the session"


async def test_always_approve_from_agent_toml_is_put_back_to_ask(home: Path, project: Path):
    (home / "agent.toml").write_text('permission = "always-approve"\n', encoding="utf-8")
    config = make_config(home, project)
    env = {**MOCK, **_write_call("unused.txt", "x")}
    async with running_gateway(config, command=mock_agent_command(), agent_env=env) as (
        _,
        platform,
    ):
        await platform.say("hello")
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        assert T("mode_forced_back", mode="always-approve", target="ask") in notices_text(platform)


async def test_strangers_are_not_heard_and_pairing_works(home: Path, project: Path):
    config = make_config(home, project)
    async with running_gateway(
        config, command=mock_agent_command(), agent_env=MOCK, paired=False
    ) as (gateway, platform):
        await platform.say("hello", user="ou_someone")
        assert platform.sent == []
        await platform.say("/pair AAAA-BBBB", user="ou_someone")
        assert T("pair_bad") in platform.sent[-1].text
        code = Pairing(gateway.store).create_code("fake")
        await platform.say(f"/pair {code}", user=OWNER)
        assert T("pair_ok") in platform.sent[-1].text
        assert gateway.store.owner("fake").tenant == TENANT
        await platform.say("/status", user="ou_someone")
        assert T("pair_ok") in platform.sent[-1].text, "a stranger still gets no answer"


async def test_a_redelivered_event_is_handled_once(home: Path, project: Path):
    config = make_config(home, project)
    async with running_gateway(config, command=mock_agent_command(), agent_env=MOCK) as (
        gateway,
        platform,
    ):
        message = InboundMessage(
            platform="fake",
            tenant=TENANT,
            chat=platform_chat(),
            message_id="om_same",
            sender_id=OWNER,
            text="hello",
            event_id="ev_same",
        )
        await gateway.on_message(message)
        await gateway.on_message(message)
        await platform.wait_for(lambda: finished_turn(platform) is not None, 120)
        turns = [s for s in platform.sent if s.card.title.startswith(T("title") + " · ")]
        assert len([t for t in turns if "Hello from mock" in t.text]) == 1


def platform_chat():
    from pi_chat_gateway.platforms.base import Chat

    return Chat("fake", "oc_dm", True)
