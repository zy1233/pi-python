"""``session/new`` takes the id the client chose (``_meta.sessionId``, the TUI's ``--session-id``),
and the responses that open a session say which directory it works in (``pi/cwd``).

A new session has no file until its first message (plan decision 17), so the tests that look at
files or at a restarted agent first have the user type something (``say``)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError, text_block

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.tests.mock_stream import mock_text_stream

INVALID_PARAMS = -32602


class QuietClient:
    """A client that takes everything the agent sends and is never asked anything."""

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        return None


def make_agent(home: Path) -> PiAcpAgent:
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=home,
        config=CliConfig(permission="ask", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )
    agent.on_connect(QuietClient())  # type: ignore[arg-type]
    return agent


def session_files(home: Path) -> list[Path]:
    return sorted((home / "sessions").glob("*.jsonl"))


async def say(agent: PiAcpAgent, session_id: str) -> None:
    """The first message of a session: the agent writes the session's file with it."""
    await agent.prompt(session_id=session_id, prompt=[text_block("hello")])


@pytest.mark.asyncio
async def test_a_new_session_takes_the_id_the_client_chose(tmp_path):
    chosen = str(uuid.uuid4())
    agent = make_agent(tmp_path)
    created = await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    assert created.session_id == chosen
    await say(agent, chosen)

    # A restarted agent finds it under that id, in the listing and by loading it.
    restarted = make_agent(tmp_path)
    assert [s.session_id for s in (await restarted.list_sessions()).sessions] == [chosen]
    assert await restarted.load_session(cwd=str(tmp_path), session_id=chosen) is not None


@pytest.mark.asyncio
async def test_without_a_chosen_id_the_agent_picks_its_own_and_ignores_the_rest_of_the_meta(
    tmp_path,
):
    agent = make_agent(tmp_path)
    # What the TUI sends besides the id, none of which names a session.
    first = await agent.new_session(cwd=str(tmp_path), yoloMode=False, autoMode=False)
    second = await agent.new_session(cwd=str(tmp_path))
    assert first.session_id != second.session_id
    await say(agent, first.session_id)
    await say(agent, second.session_id)
    assert len(session_files(tmp_path)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param(str.upper, id="upper-case"),
        pytest.param(lambda s: "{" + s + "}", id="braces"),
        pytest.param(lambda s: s.replace("-", ""), id="no-hyphens"),
        pytest.param(lambda s: "urn:uuid:" + s, id="urn"),
    ],
)
async def test_the_id_comes_back_in_the_canonical_form(tmp_path, spelling: Callable[[str], str]):
    chosen = uuid.uuid4()
    agent = make_agent(tmp_path)
    created = await agent.new_session(cwd=str(tmp_path), sessionId=spelling(str(chosen)))
    assert created.session_id == str(chosen)
    await say(agent, created.session_id)
    assert session_files(tmp_path)[0].name.endswith(f"-{chosen}.jsonl")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "../../outside",
        "12345678-1234-1234-1234-12345678",
        42,
        True,
        ["x"],
        {"id": "x"},
    ],
)
async def test_an_id_that_is_not_a_uuid_is_refused_and_nothing_is_created(tmp_path, bad: Any):
    agent = make_agent(tmp_path)
    with pytest.raises(RequestError) as refused:
        await agent.new_session(cwd=str(tmp_path), sessionId=bad)
    assert refused.value.code == INVALID_PARAMS
    assert session_files(tmp_path) == []
    assert (await agent.list_sessions()).sessions == []


@pytest.mark.asyncio
async def test_an_id_in_use_is_refused_and_the_session_that_has_it_is_left_alone(tmp_path):
    agent = make_agent(tmp_path)
    chosen = str(uuid.uuid4())
    await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    await say(agent, chosen)
    (before,) = session_files(tmp_path)
    content = before.read_bytes()

    with pytest.raises(RequestError) as refused:
        await agent.new_session(cwd=str(tmp_path), sessionId=chosen.upper())  # another spelling
    assert refused.value.code == INVALID_PARAMS
    with pytest.raises(RequestError):  # and after a restart
        await make_agent(tmp_path).new_session(cwd=str(tmp_path), sessionId=chosen)

    assert session_files(tmp_path) == [before]
    assert before.read_bytes() == content


@pytest.mark.asyncio
async def test_an_id_in_use_by_a_session_from_another_directory_is_refused_too(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    agent = make_agent(tmp_path)
    chosen = str(uuid.uuid4())
    await agent.new_session(cwd=str(tmp_path / "one"), sessionId=chosen)
    await say(agent, chosen)
    with pytest.raises(RequestError):
        await agent.new_session(cwd=str(tmp_path / "two"), sessionId=chosen)
    assert len(session_files(tmp_path)) == 1


@pytest.mark.asyncio
async def test_several_requests_for_the_same_id_at_once_make_exactly_one_session(tmp_path):
    agent = make_agent(tmp_path)
    chosen = str(uuid.uuid4())
    outcomes = await asyncio.gather(
        *(agent.new_session(cwd=str(tmp_path), sessionId=chosen) for _ in range(5)),
        return_exceptions=True,
    )
    made = [o for o in outcomes if not isinstance(o, BaseException)]
    refused = [o for o in outcomes if isinstance(o, RequestError)]
    assert len(made) == 1 and len(refused) == 4, outcomes
    await say(agent, chosen)
    assert len(session_files(tmp_path)) == 1


@pytest.mark.asyncio
async def test_the_responses_that_open_a_session_name_the_directory_it_works_in(tmp_path):
    made_in = tmp_path / "made-in"
    asked_from = tmp_path / "asked-from"
    made_in.mkdir()
    asked_from.mkdir()

    maker = make_agent(tmp_path)
    created = await maker.new_session(cwd=str(made_in))
    assert created.field_meta is not None
    assert created.field_meta["pi/cwd"] == str(made_in)
    await say(maker, created.session_id)

    # The saved directory wins over the one the client sent, and the response says so.
    loaded = await make_agent(tmp_path).load_session(
        cwd=str(asked_from), session_id=created.session_id
    )
    assert loaded is not None and loaded.field_meta is not None
    assert loaded.field_meta["pi/cwd"] == str(made_in)

    resumed = await make_agent(tmp_path).resume_session(
        cwd=str(asked_from), session_id=created.session_id
    )
    assert resumed.field_meta is not None
    assert resumed.field_meta["pi/cwd"] == str(made_in)
    # The model hints that were there before are still there.
    assert resumed.field_meta["pi/currentModelId"] == "mock"


@pytest.mark.asyncio
async def test_an_id_stays_taken_while_its_session_has_no_file_and_is_free_when_it_closes(
    tmp_path,
):
    agent = make_agent(tmp_path)
    chosen = str(uuid.uuid4())
    await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    assert session_files(tmp_path) == []  # nobody typed yet: no file to say the id is taken

    with pytest.raises(RequestError) as refused:
        await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    assert refused.value.code == INVALID_PARAMS

    await agent.close_session(session_id=chosen)
    again = await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    assert again.session_id == chosen


@pytest.mark.asyncio
async def test_deleting_a_session_that_has_no_file_frees_its_id(tmp_path):
    agent = make_agent(tmp_path)
    chosen = str(uuid.uuid4())
    await agent.new_session(cwd=str(tmp_path), sessionId=chosen)

    deleted = await agent.ext_method("pi/session/delete", {"sessionId": chosen})
    assert deleted == {"sessionId": chosen, "deleted": True}
    with pytest.raises(RequestError):
        await say(agent, chosen)  # the session is gone

    again = await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    assert again.session_id == chosen


@pytest.mark.asyncio
async def test_an_id_that_another_process_took_meanwhile_is_refused_when_the_session_is_saved(
    tmp_path,
):
    chosen = str(uuid.uuid4())
    agent = make_agent(tmp_path)
    await agent.new_session(cwd=str(tmp_path), sessionId=chosen)  # no file yet

    # A second agent process cannot see that: it makes a session with the same id and saves it.
    other = make_agent(tmp_path)
    await other.new_session(cwd=str(tmp_path), sessionId=chosen)
    await say(other, chosen)
    (theirs,) = session_files(tmp_path)

    # Saving ours would put a second session with that id beside theirs: refused at the write.
    for _ in range(2):  # the refusal leaves the session as it was, so it holds the next time too
        with pytest.raises(RequestError) as refused:
            await say(agent, chosen)
        assert refused.value.code == INVALID_PARAMS
    assert session_files(tmp_path) == [theirs]


@pytest.mark.asyncio
async def test_a_session_with_a_chosen_id_takes_more_than_one_message(tmp_path):
    # The look for an id that another process took is made before the first message only: the
    # session's own file is no reason to refuse its next message.
    chosen = str(uuid.uuid4())
    agent = make_agent(tmp_path)
    await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    for _ in range(3):
        await say(agent, chosen)

    (path,) = session_files(tmp_path)
    entries = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    roles = [entry["message"]["role"] for entry in entries if entry.get("type") == "message"]
    assert roles.count("user") == 3


@pytest.mark.asyncio
async def test_a_session_that_could_not_be_opened_does_not_keep_its_id(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    chosen = str(uuid.uuid4())

    async def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cannot bind")

    with monkeypatch.context() as patched:
        patched.setattr(agent, "_bind_session", failing)
        with pytest.raises(RuntimeError, match="cannot bind"):
            await agent.new_session(cwd=str(tmp_path), sessionId=chosen)

    again = await agent.new_session(cwd=str(tmp_path), sessionId=chosen)
    assert again.session_id == chosen
