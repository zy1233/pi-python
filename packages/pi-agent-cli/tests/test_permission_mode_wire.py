"""The permission mode on the ACP wire.

A client sets and reads the mode of a session in four ways, and these tests assert each:

* the ``mode`` config option (``session/set_config_option``): the standard way;
* ``modes`` in the response that opens a session, and ``session/set_mode``;
* ``_meta.autoMode`` and ``_meta.yoloMode`` in ``session/new``, ``session/load`` and
  ``session/resume`` (the pager's two flags, kept as they are);
* the notification ``pi/yolo_mode_changed`` (the pager's setting; it has no session id).

``always-approve`` is not a choice that the standard items offer (design decision D6).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError, text_block
from acp.schema import AllowedOutcome, RequestPermissionResponse

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.auto_mode import ENTRY_MESSAGE
from pi_agent_cli.config import AutoConfig, CliConfig
from pi_agent_core.tests.mock_stream import mock_text_stream


class _Client:
    def __init__(self) -> None:
        self.updates: list[Any] = []
        self.requests: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update))

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.requests.append(tool_call)
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
        )

    def kinds(self) -> list[str]:
        return [getattr(update, "session_update", "") for _, update in self.updates]

    def messages(self, session_id: str | None = None) -> list[str]:
        return [
            getattr(getattr(update, "content", None), "text", "") or ""
            for sid, update in self.updates
            if getattr(update, "session_update", None) == "agent_message_chunk"
            and (session_id is None or sid == session_id)
        ]

    def option_updates(self, session_id: str | None = None) -> list[Any]:
        return [
            update
            for sid, update in self.updates
            if getattr(update, "session_update", None) == "config_option_update"
            and (session_id is None or sid == session_id)
        ]


def _agent(tmp_path: Path, permission: str = "ask", **config: Any) -> tuple[PiAcpAgent, _Client]:
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path / "home",
        config=CliConfig(
            permission=permission,  # type: ignore[arg-type]
            provider="mock",
            model_id="mock",
            **config,
        ),
    )
    client = _Client()
    agent.on_connect(client)
    return agent, client


def _project(tmp_path: Path, name: str = "project") -> str:
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    return str(path)


async def _settled(agent: PiAcpAgent) -> None:
    await asyncio.gather(*agent._background_tasks)


def _mode_option(options: Any) -> Any:
    (option,) = [o for o in options or [] if o.id == "mode"]
    return option


def _values(option: Any) -> list[str]:
    return [o.value for o in option.options]


# ------------------------------------------------------------- what a session response says


@pytest.mark.parametrize("method", ["new", "load", "resume"])
async def test_every_response_that_opens_a_session_carries_the_mode(tmp_path: Path, method: str):
    agent, _ = _agent(tmp_path)
    cwd = _project(tmp_path)
    created = await agent.new_session(cwd=cwd)
    await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])  # saved: loadable
    if method == "new":
        response: Any = created
    elif method == "load":
        response = await agent.load_session(cwd=cwd, session_id=created.session_id)
    else:
        response = await agent.resume_session(session_id=created.session_id, cwd=cwd)

    assert response.modes.current_mode_id == "ask"
    assert [mode.id for mode in response.modes.available_modes] == ["ask", "auto"]
    option = _mode_option(response.config_options)
    assert option.category == "mode"
    assert option.type == "select"
    assert option.current_value == "ask"
    assert _values(option) == ["ask", "auto"]


async def test_the_model_option_is_still_there_beside_the_mode_option(tmp_path: Path):
    agent, _ = _agent(tmp_path)

    created = await agent.new_session(cwd=_project(tmp_path))

    assert [option.id for option in created.config_options] == ["model", "mode"]


async def test_a_session_starts_in_the_mode_of_agent_toml(tmp_path: Path):
    agent, _ = _agent(tmp_path, permission="auto")

    created = await agent.new_session(cwd=_project(tmp_path))

    assert created.modes.current_mode_id == "auto"
    assert _mode_option(created.config_options).current_value == "auto"


async def test_always_approve_is_shown_as_the_current_value_but_not_offered(tmp_path: Path):
    agent, _ = _agent(tmp_path, permission="always-approve")

    created = await agent.new_session(cwd=_project(tmp_path))

    assert created.modes.current_mode_id == "always-approve"
    option = _mode_option(created.config_options)
    assert option.current_value == "always-approve"
    # The current value is one of the listed ones; a session in ``ask`` is not offered it.
    assert _values(option) == ["ask", "auto", "always-approve"]
    other, _ = _agent(tmp_path / "other")
    plain = await other.new_session(cwd=_project(tmp_path, "other-project"))
    assert "always-approve" not in _values(_mode_option(plain.config_options))


# ------------------------------------------------------------------- set_config_option / set_mode


async def test_the_mode_option_puts_a_session_in_auto_and_back(tmp_path: Path):
    agent, client = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))
    session_id = created.session_id
    await _settled(agent)
    harness = agent._harnesses[session_id]
    assert harness.tool_execution == "parallel"

    answer = await agent.set_config_option(config_id="mode", session_id=session_id, value="auto")

    assert _mode_option(answer.config_options).current_value == "auto"
    assert harness.tool_execution == "sequential"  # Auto runs the calls one by one
    assert any(ENTRY_MESSAGE in text for text in client.messages(session_id))

    answer = await agent.set_config_option(config_id="mode", session_id=session_id, value="ask")

    assert _mode_option(answer.config_options).current_value == "ask"
    assert harness.tool_execution == "parallel"


async def test_the_entry_message_is_sent_each_time_auto_is_entered_and_not_when_it_is_set_again(
    tmp_path: Path,
):
    agent, client = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))
    await _settled(agent)
    session_id = created.session_id

    def entry_messages() -> int:
        return len([text for text in client.messages(session_id) if ENTRY_MESSAGE in text])

    async def select(value: str) -> None:
        await agent.set_config_option(config_id="mode", session_id=session_id, value=value)

    assert entry_messages() == 0
    await select("auto")
    assert entry_messages() == 1
    await select("auto")  # nothing changes
    assert entry_messages() == 1
    await select("ask")
    assert entry_messages() == 1
    await select("auto")  # entered again
    assert entry_messages() == 2


@pytest.mark.parametrize("value", ["always-approve", "plan", "bogus", "", "AUTO"])
async def test_the_mode_option_refuses_what_it_does_not_offer(tmp_path: Path, value: str):
    agent, _ = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))

    with pytest.raises(RequestError) as refused:
        await agent.set_config_option(config_id="mode", session_id=created.session_id, value=value)

    assert refused.value.code == -32602
    assert agent._session_mode(created.session_id) == "ask"  # nothing changed


async def test_the_mode_option_takes_the_current_value_back_without_a_change(tmp_path: Path):
    """A client that sends the value it was told is not refused, ``always-approve`` included."""
    agent, _ = _agent(tmp_path, permission="always-approve")
    created = await agent.new_session(cwd=_project(tmp_path))

    answer = await agent.set_config_option(
        config_id="mode", session_id=created.session_id, value="always-approve"
    )

    assert _mode_option(answer.config_options).current_value == "always-approve"


async def test_a_session_in_always_approve_can_be_moved_to_auto_and_ask(tmp_path: Path):
    agent, _ = _agent(tmp_path, permission="always-approve")
    created = await agent.new_session(cwd=_project(tmp_path))

    await agent.set_config_option(config_id="mode", session_id=created.session_id, value="auto")
    assert agent._session_mode(created.session_id) == "auto"
    await agent.set_config_option(config_id="mode", session_id=created.session_id, value="ask")
    assert agent._session_mode(created.session_id) == "ask"


async def test_the_mode_of_a_session_the_agent_does_not_have_is_refused(tmp_path: Path):
    agent, _ = _agent(tmp_path)

    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="mode", session_id="no-such-session", value="auto")
    with pytest.raises(RequestError):
        await agent.set_session_mode(session_id="no-such-session", mode_id="auto")


async def test_session_set_mode_is_the_same_state_as_the_option(tmp_path: Path):
    agent, _ = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))

    await agent.set_session_mode(session_id=created.session_id, mode_id="auto")

    assert agent._session_mode(created.session_id) == "auto"
    assert agent._harnesses[created.session_id].tool_execution == "sequential"
    again = await agent.resume_session(session_id=created.session_id, cwd=_project(tmp_path))
    # The session is blank and open here already: the response tells the mode it is in.
    assert again.modes.current_mode_id == "auto"
    assert _mode_option(again.config_options).current_value == "auto"


@pytest.mark.parametrize("mode_id", ["always-approve", "plan", "bogus", "acceptEdits"])
async def test_session_set_mode_refuses_what_it_does_not_offer(tmp_path: Path, mode_id: str):
    agent, _ = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))

    with pytest.raises(RequestError) as refused:
        await agent.set_session_mode(session_id=created.session_id, mode_id=mode_id)

    assert refused.value.code == -32602
    assert agent._session_mode(created.session_id) == "ask"


async def test_the_mode_is_the_state_of_one_session(tmp_path: Path):
    agent, _ = _agent(tmp_path)
    first = await agent.new_session(cwd=_project(tmp_path, "one"))
    second = await agent.new_session(cwd=_project(tmp_path, "two"))

    await agent.set_config_option(config_id="mode", session_id=first.session_id, value="auto")

    assert agent._session_mode(first.session_id) == "auto"
    assert agent._session_mode(second.session_id) == "ask"
    assert agent._harnesses[second.session_id].tool_execution == "parallel"


async def test_closing_a_session_forgets_its_mode(tmp_path: Path):
    agent, _ = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))
    await agent.set_config_option(config_id="mode", session_id=created.session_id, value="auto")

    await agent.close_session(session_id=created.session_id)

    assert created.session_id not in agent._session_modes
    assert created.session_id not in agent._autos


# --------------------------------------------------------------------- the pager's two flags


@pytest.mark.parametrize(
    ("meta", "configured", "expected"),
    [
        ({"autoMode": True}, "ask", "auto"),
        ({"yoloMode": True}, "ask", "always-approve"),
        (
            {"autoMode": True, "yoloMode": True},
            "ask",
            "always-approve",
        ),  # yolo wins, as in the pager
        ({"autoMode": False, "yoloMode": False}, "ask", "ask"),
        ({}, "ask", "ask"),
        ({"autoMode": "yes"}, "ask", "ask"),  # only a true boolean counts
        # Both flags false say nothing: they must not take away the user's own setting.
        ({"autoMode": False, "yoloMode": False}, "always-approve", "always-approve"),
        ({"autoMode": False, "yoloMode": False}, "auto", "auto"),
        ({"autoMode": True}, "always-approve", "auto"),
    ],
)
async def test_the_flags_of_the_pager_choose_the_start_mode(
    tmp_path: Path, meta: dict[str, Any], configured: str, expected: str
):
    agent, _ = _agent(tmp_path, permission=configured)

    created = await agent.new_session(cwd=_project(tmp_path), **meta)

    assert agent._session_mode(created.session_id) == expected
    assert created.modes.current_mode_id == expected


@pytest.mark.parametrize("method", ["load", "resume"])
async def test_the_flags_of_the_pager_are_read_when_a_session_is_loaded_or_resumed(
    tmp_path: Path, method: str
):
    agent, _ = _agent(tmp_path)
    cwd = _project(tmp_path)
    created = await agent.new_session(cwd=cwd)
    await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
    await agent.close_session(session_id=created.session_id)

    if method == "load":
        response = await agent.load_session(cwd=cwd, session_id=created.session_id, autoMode=True)
    else:
        response = await agent.resume_session(session_id=created.session_id, cwd=cwd, autoMode=True)

    assert response.modes.current_mode_id == "auto"
    assert agent._harnesses[created.session_id].tool_execution == "sequential"


async def test_the_start_message_of_auto_is_sent_to_a_session_that_starts_in_auto(tmp_path: Path):
    agent, client = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path), autoMode=True)
    await _settled(agent)

    assert any(ENTRY_MESSAGE in text for text in client.messages(created.session_id))


async def test_a_session_from_agent_toml_auto_says_what_the_setting_means_now(tmp_path: Path):
    agent, client = _agent(tmp_path, permission="auto")
    created = await agent.new_session(cwd=_project(tmp_path))
    await _settled(agent)

    text = "\n".join(client.messages(created.session_id))
    assert 'permission = "always-approve"' in text  # it used to mean "no question"

    other, other_client = _agent(tmp_path / "x")
    chosen = await other.new_session(cwd=_project(tmp_path, "y"), autoMode=True)
    await _settled(other)
    assert 'permission = "always-approve"' not in "\n".join(
        other_client.messages(chosen.session_id)
    )


# ------------------------------------------------------------ the legacy notification


async def test_the_legacy_notification_moves_every_open_session_and_tells_the_client(
    tmp_path: Path,
):
    agent, client = _agent(tmp_path)
    first = await agent.new_session(cwd=_project(tmp_path, "one"))
    second = await agent.new_session(cwd=_project(tmp_path, "two"))
    await _settled(agent)

    await agent.ext_notification("pi/yolo_mode_changed", {"permission_mode": "auto"})

    for created in (first, second):
        assert agent._session_mode(created.session_id) == "auto"
        (update,) = client.option_updates(created.session_id)
        assert _mode_option(update.config_options).current_value == "auto"
        assert agent._harnesses[created.session_id].tool_execution == "sequential"


async def test_the_legacy_notification_sets_the_start_mode_of_later_sessions(tmp_path: Path):
    agent, _ = _agent(tmp_path)

    await agent.ext_notification("pi/yolo_mode_changed", {"permission_mode": "always-approve"})
    created = await agent.new_session(cwd=_project(tmp_path))

    assert created.modes.current_mode_id == "always-approve"


@pytest.mark.parametrize("payload", [{"permission_mode": "bogus"}, {}, {"permission_mode": 3}])
async def test_the_legacy_notification_ignores_what_it_does_not_understand(
    tmp_path: Path, payload: dict[str, Any]
):
    agent, client = _agent(tmp_path)
    created = await agent.new_session(cwd=_project(tmp_path))

    await agent.ext_notification("pi/yolo_mode_changed", payload)
    await agent.ext_notification("pi/something_else", {"permission_mode": "auto"})

    assert agent._session_mode(created.session_id) == "ask"
    assert client.option_updates() == []


async def test_a_notification_that_changes_nothing_sends_nothing(tmp_path: Path):
    agent, client = _agent(tmp_path)
    await agent.new_session(cwd=_project(tmp_path))

    await agent.ext_notification("pi/yolo_mode_changed", {"permission_mode": "ask"})

    assert client.option_updates() == []


# -------------------------------------------------------------------- what is read from [auto]


async def test_the_values_of_auto_that_are_not_used_are_told_to_the_user(tmp_path: Path):
    auto = AutoConfig(notes=('The key "colour" in [auto] is not known, and it is ignored.',))
    agent, client = _agent(tmp_path, auto=auto)
    created = await agent.new_session(cwd=_project(tmp_path))
    await _settled(agent)

    text = "\n".join(client.messages(created.session_id))
    assert "colour" in text
