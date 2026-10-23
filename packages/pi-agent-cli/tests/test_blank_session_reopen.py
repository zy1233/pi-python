"""A session in which nobody typed has no file (plan decision 17), but an editor can hold its id.

Zed saves the id of every thread that shows something, and the notices of this agent are
something (the ignored MCP servers, the question about the project). When Zed starts again it
asks for such a thread with ``session/load``. It does the same when the thread is opened from the
history. The agent used to answer ``resource_not_found``: the thread was dead ("Failed to
Launch", and Retry failed the same way). Now an id of the agent's own kind opens as an empty
session. It still has no file until its first message.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError, text_block
from acp.schema import McpServerStdio

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import TRUST_ENV
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.tests.mock_stream import mock_text_stream

INVALID_PARAMS = -32602
RESOURCE_NOT_FOUND = -32002

# The id from the bug report: the blank thread that Zed could not load.
REPORTED = "01a1627f-ff3c-70c5-8f14-5165d5042520"

MESSAGE_KINDS = ("user_message_chunk", "agent_message_chunk")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    # What is installed or set on this machine must not add a notice of its own.
    monkeypatch.delenv(TRUST_ENV, raising=False)
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])


class Recorder:
    """A client that records what the agent sends and is never asked anything."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, Any]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append((session_id, update))

    def messages(self, kind: str | None = None) -> list[tuple[str, str]]:
        """``(kind, text)`` of what the client would show as messages, in order."""
        return [
            (update.session_update, getattr(update.content, "text", "") or "")
            for _, update in self.updates
            if getattr(update, "session_update", None) in MESSAGE_KINDS
            and (kind is None or update.session_update == kind)
        ]


def make_agent(home: Path) -> tuple[PiAcpAgent, Recorder]:
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=home,
        config=CliConfig(permission="ask", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )
    client = Recorder()
    agent.on_connect(client)  # type: ignore[arg-type]
    return agent, client


def session_files(home: Path) -> list[Path]:
    return sorted((home / "sessions").glob("*.jsonl"))


async def say(agent: PiAcpAgent, session_id: str) -> None:
    """The first message of a session: the agent writes the session's file with it."""
    await agent.prompt(session_id=session_id, prompt=[text_block("hello")])


async def settle(agent: PiAcpAgent) -> None:
    # Notices go out after the response has flushed (Zed drops earlier ones).
    await asyncio.gather(*agent._background_tasks)


async def listed_ids(agent: PiAcpAgent) -> list[str]:
    return [s.session_id for s in (await agent.list_sessions()).sessions]


@pytest.mark.asyncio
async def test_a_blank_session_opens_again_after_a_restart_and_is_saved_with_its_first_message(
    tmp_path,
):
    first, _ = make_agent(tmp_path)
    session_id = (await first.new_session(cwd=str(tmp_path))).session_id
    assert session_files(tmp_path) == []

    restarted, client = make_agent(tmp_path)  # the editor started again and asks for its thread
    loaded = await restarted.load_session(cwd=str(tmp_path), session_id=session_id)
    assert loaded is not None
    await settle(restarted)
    assert client.messages() == []  # nothing to replay, and nothing to say
    assert session_files(tmp_path) == []  # opening it writes nothing
    assert await listed_ids(restarted) == []  # and it is still not in the list

    await say(restarted, session_id)  # it is a working session
    (path,) = session_files(tmp_path)
    assert session_id in path.name
    assert await listed_ids(restarted) == [session_id]

    # From now on it is an ordinary session: the next start replays it.
    again, replay = make_agent(tmp_path)
    assert await again.load_session(cwd=str(tmp_path), session_id=session_id) is not None
    assert replay.messages("user_message_chunk") == [("user_message_chunk", "hello")]


@pytest.mark.asyncio
async def test_resume_opens_a_blank_session_too(tmp_path):
    first, _ = make_agent(tmp_path)
    session_id = (await first.new_session(cwd=str(tmp_path))).session_id

    restarted, client = make_agent(tmp_path)
    resumed = await restarted.resume_session(session_id=session_id, cwd=str(tmp_path))
    assert resumed is not None
    await settle(restarted)
    assert client.messages() == []
    assert session_files(tmp_path) == []

    await say(restarted, session_id)
    assert len(session_files(tmp_path)) == 1


@pytest.mark.asyncio
async def test_the_reopened_blank_session_works_in_the_directory_the_client_names(tmp_path):
    first, _ = make_agent(tmp_path)
    session_id = (await first.new_session(cwd=str(tmp_path))).session_id
    project = tmp_path / "project"
    project.mkdir()

    restarted, _ = make_agent(tmp_path)
    loaded = await restarted.load_session(cwd=str(project), session_id=session_id)
    assert loaded.field_meta is not None
    assert Path(loaded.field_meta["pi/cwd"]) == project
    assert loaded.field_meta["pi/currentModelId"] == "mock"
    assert loaded.config_options  # the model choice is there as it is for a new session


@pytest.mark.asyncio
async def test_the_servers_the_client_passes_are_reported_ignored_for_the_reopened_session(
    tmp_path,
):
    agent, client = make_agent(tmp_path)
    context7 = McpServerStdio(name="mcp-server-context7", command="npx", args=[], env=[])
    await agent.load_session(cwd=str(tmp_path), session_id=REPORTED, mcp_servers=[context7])
    await settle(agent)

    (notice,) = [text for _, text in client.messages("agent_message_chunk")]
    assert "(mcp-server-context7)" in notice


@pytest.mark.asyncio
async def test_a_blank_session_that_is_open_here_stays_as_it_is(tmp_path):
    # Zed opens a thread from its history while the agent still has it open.
    agent, _ = make_agent(tmp_path)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    harness = agent._harnesses[session_id]

    assert await agent.load_session(cwd=str(tmp_path), session_id=session_id) is not None
    assert await agent.resume_session(session_id=session_id, cwd=str(tmp_path)) is not None
    assert agent._harnesses[session_id] is harness  # not opened a second time

    await say(agent, session_id)
    assert len(session_files(tmp_path)) == 1


@pytest.mark.asyncio
async def test_a_blank_session_that_was_closed_opens_again(tmp_path):
    # Zed closes the session when its thread view goes away, and loads it when the thread is
    # opened again.
    agent, _ = make_agent(tmp_path)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await agent.close_session(session_id=session_id)
    with pytest.raises(RequestError):  # closed: a prompt has no session to go to
        await say(agent, session_id)

    assert await agent.load_session(cwd=str(tmp_path), session_id=session_id) is not None
    await say(agent, session_id)
    assert len(session_files(tmp_path)) == 1


@pytest.mark.asyncio
async def test_the_id_from_the_bug_report_opens(tmp_path):
    # No file holds this id, and no process has it open: the agent cannot tell it from the id of
    # a session in which nobody typed, so it opens it, empty.
    agent, _ = make_agent(tmp_path)
    assert await agent.load_session(cwd=str(tmp_path), session_id=REPORTED) is not None
    assert session_files(tmp_path) == []
    await say(agent, REPORTED)
    (path,) = session_files(tmp_path)
    assert REPORTED in path.name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_id",
    [
        "no-such-session",
        "",
        "../../outside",
        REPORTED.upper(),  # a UUID, but not in the form the agent gives out
        REPORTED.replace("-", ""),
        f"urn:uuid:{REPORTED}",
    ],
)
async def test_an_id_that_is_not_of_the_agents_kind_is_still_unknown(tmp_path, session_id):
    agent, _ = make_agent(tmp_path)
    with pytest.raises(RequestError) as loading:
        await agent.load_session(cwd=str(tmp_path), session_id=session_id)
    assert loading.value.code == RESOURCE_NOT_FOUND
    with pytest.raises(RequestError) as resuming:
        await agent.resume_session(session_id=session_id, cwd=str(tmp_path))
    assert resuming.value.code == RESOURCE_NOT_FOUND
    assert agent._harnesses == {}  # nothing was opened
    assert session_files(tmp_path) == []


@pytest.mark.asyncio
async def test_a_session_with_a_file_is_loaded_from_it_and_not_opened_empty(tmp_path):
    first, _ = make_agent(tmp_path)
    session_id = (await first.new_session(cwd=str(tmp_path))).session_id
    await say(first, session_id)
    (path,) = session_files(tmp_path)

    restarted, client = make_agent(tmp_path)
    assert await restarted.load_session(cwd=str(tmp_path), session_id=session_id) is not None
    assert client.messages("user_message_chunk") == [("user_message_chunk", "hello")]  # replayed
    assert session_files(tmp_path) == [path]  # and no second file


@pytest.mark.asyncio
async def test_a_blank_session_that_another_process_saved_meanwhile_is_not_saved_twice(tmp_path):
    # The process that made the session saves it first. The one that opened it again has the
    # id kept (as for a chosen id), so its first message is refused: a second file with the
    # same id would answer a later session/load as well as the first. (The process that made
    # the session does not look: that check would cost every session a directory listing.)
    made, _ = make_agent(tmp_path)
    session_id = (await made.new_session(cwd=str(tmp_path))).session_id
    reopened, _ = make_agent(tmp_path)
    assert await reopened.load_session(cwd=str(tmp_path), session_id=session_id) is not None

    await say(made, session_id)
    (theirs,) = session_files(tmp_path)
    for _ in range(2):  # the refusal leaves the session as it was, so it holds the next time too
        with pytest.raises(RequestError) as refused:
            await say(reopened, session_id)
        assert refused.value.code == INVALID_PARAMS
    assert session_files(tmp_path) == [theirs]


@pytest.mark.asyncio
async def test_a_blank_session_that_could_not_be_opened_does_not_keep_its_id(tmp_path, monkeypatch):
    agent, _ = make_agent(tmp_path)

    async def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cannot bind")

    with monkeypatch.context() as patched:
        patched.setattr(agent, "_bind_session", failing)
        with pytest.raises(RuntimeError, match="cannot bind"):
            await agent.load_session(cwd=str(tmp_path), session_id=REPORTED)

    assert agent._harnesses == {}
    assert await agent.load_session(cwd=str(tmp_path), session_id=REPORTED) is not None


@pytest.mark.asyncio
async def test_a_reopened_blank_session_is_closed_and_freed_like_any_other(tmp_path):
    agent, _ = make_agent(tmp_path)
    await agent.load_session(cwd=str(tmp_path), session_id=REPORTED)
    assert REPORTED in agent._reserved_ids  # the id stays taken while the session has no file

    await agent.close_session(session_id=REPORTED)
    assert REPORTED not in agent._reserved_ids
    assert agent._harnesses == {}
    # A client may choose the id for a new session now.
    again = await agent.new_session(cwd=str(tmp_path), sessionId=REPORTED)
    assert again.session_id == str(uuid.UUID(REPORTED))
