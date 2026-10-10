"""``session/list`` pages with an opaque cursor, and session lookups by id do not read every
session file."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from acp import RequestError

from pi_agent_cli import agent as agent_module
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.session_list import SESSION_LIST_PAGE_SIZE, decode_cursor, encode_cursor
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import JsonlSessionRepo, LocalExecutionEnv

PROJECT = "/work/project"
FILE_NAME = "20261001T100500000Z-abc.jsonl"


def write_session(sessions: Path, session_id: str, minute: int, cwd: str = PROJECT) -> str:
    created = f"2026-10-01T{minute // 60:02d}:{minute % 60:02d}:00.000Z"
    name = f"{created.replace(':', '').replace('-', '').replace('.', '')}-{session_id}.jsonl"
    header = {"type": "session", "version": 3, "id": session_id, "timestamp": created, "cwd": cwd}
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / name).write_text(json.dumps(header) + "\n", encoding="utf-8")
    return name


def make_agent(home: Path, repo: JsonlSessionRepo | None = None) -> PiAcpAgent:
    return PiAcpAgent(
        stream_fn=mock_text_stream,
        home=home,
        repo=repo,
        config=CliConfig(permission="ask", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )


async def all_pages(agent: PiAcpAgent, **params) -> list[list[str]]:
    pages: list[list[str]] = []
    cursor = None
    while True:
        response = await agent.list_sessions(cursor=cursor, **params)
        pages.append([s.session_id for s in response.sessions])
        cursor = response.next_cursor
        if cursor is None:
            return pages
        assert pages[-1], "a page that carries a cursor holds something here"


class CountingEnv(LocalExecutionEnv):
    def __init__(self, cwd: str | Path) -> None:
        super().__init__(cwd)
        self.read: list[str] = []

    async def read_text_lines(self, path, max_lines=None):
        self.read.append(Path(path).name)
        return await super().read_text_lines(path, max_lines)


def test_cursors_round_trip_and_are_opaque_strings():
    cursor = encode_cursor(FILE_NAME)
    assert isinstance(cursor, str) and FILE_NAME not in cursor
    assert decode_cursor(cursor) == FILE_NAME
    assert "=" not in cursor  # no padding to trip over in a URL or a log line


def raw(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not base64!!",
        "AAAA",  # base64 of bytes that are not JSON
        raw([FILE_NAME]),
        raw({}),
        raw({"after": 7}),
        raw({"after": None}),
        raw({"after": ""}),
        raw({"after": "no-extension"}),
        raw({"after": "../" + FILE_NAME}),
        raw({"after": "dir\\" + FILE_NAME}),
        raw({"after": "x" * 300 + ".jsonl"}),
        raw({"after": "bad\0" + FILE_NAME}),
    ],
)
def test_strings_that_are_not_cursors_are_refused(cursor):
    with pytest.raises(ValueError):
        decode_cursor(cursor)


async def test_list_sessions_pages_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "SESSION_LIST_PAGE_SIZE", 2)
    for minute in range(5):
        write_session(tmp_path / "sessions", f"s{minute}", minute)
    agent = make_agent(tmp_path)

    first = await agent.list_sessions()
    assert [s.session_id for s in first.sessions] == ["s4", "s3"]
    second = await agent.list_sessions(cursor=first.next_cursor)
    assert [s.session_id for s in second.sessions] == ["s2", "s1"]
    last = await agent.list_sessions(cursor=second.next_cursor)
    assert [s.session_id for s in last.sessions] == ["s0"]
    assert (first.next_cursor is None, second.next_cursor is None, last.next_cursor) == (
        False,
        False,
        None,
    )
    assert await all_pages(agent) == [["s4", "s3"], ["s2", "s1"], ["s0"]]


async def test_list_sessions_pages_keep_to_the_directory_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "SESSION_LIST_PAGE_SIZE", 2)
    for minute in range(8):
        write_session(tmp_path / "sessions", f"s{minute}", minute, PROJECT if minute % 2 else "/o")
    agent = make_agent(tmp_path)
    assert await all_pages(agent, cwd=PROJECT) == [["s7", "s5"], ["s3", "s1"]]
    assert await all_pages(agent, cwd="/nowhere") == [[]]


async def test_a_cursor_works_on_another_agent_instance(tmp_path, monkeypatch):
    """The cursor holds the position itself; the agent keeps no list between requests."""
    monkeypatch.setattr(agent_module, "SESSION_LIST_PAGE_SIZE", 2)
    for minute in range(5):
        write_session(tmp_path / "sessions", f"s{minute}", minute)
    cursor = (await make_agent(tmp_path).list_sessions()).next_cursor
    resumed = await make_agent(tmp_path).list_sessions(cursor=cursor)
    assert [s.session_id for s in resumed.sessions] == ["s2", "s1"]


async def test_the_default_page_is_bounded(tmp_path):
    for minute in range(SESSION_LIST_PAGE_SIZE + 5):
        write_session(tmp_path / "sessions", f"s{minute:03d}", minute)
    agent = make_agent(tmp_path)
    first = await agent.list_sessions()
    assert len(first.sessions) == SESSION_LIST_PAGE_SIZE and first.next_cursor is not None
    assert [len(p) for p in await all_pages(agent)] == [SESSION_LIST_PAGE_SIZE, 5]


async def test_list_sessions_refuses_a_cursor_it_did_not_issue(tmp_path):
    write_session(tmp_path / "sessions", "s1", 1)
    agent = make_agent(tmp_path)
    for cursor in ["garbage", raw({"after": "../../etc/passwd"}), raw({"after": 3})]:
        with pytest.raises(RequestError) as exc:
            await agent.list_sessions(cursor=cursor)
        assert exc.value.code == -32602


async def test_a_page_has_titles_and_times(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "SESSION_LIST_PAGE_SIZE", 1)
    write_session(tmp_path / "sessions", "s1", 1)
    write_session(tmp_path / "sessions", "s2", 2)
    page = await make_agent(tmp_path).list_sessions()
    [info] = page.sessions
    assert (info.session_id, info.cwd, info.title) == ("s2", PROJECT, "(no messages)")
    assert info.updated_at and page.next_cursor


async def test_looking_a_session_up_reads_one_header_not_all(tmp_path):
    sessions = tmp_path / "sessions"
    for minute in range(40):
        write_session(sessions, f"s{minute:02d}", minute)
    env = CountingEnv(tmp_path)
    agent = make_agent(tmp_path, JsonlSessionRepo(sessions, env))
    loaded = await agent.load_session(cwd=PROJECT, session_id="s07")
    assert loaded is not None
    # One header to find the session, one more when the storage opens it; not forty.
    assert len(env.read) <= 3

    with pytest.raises(RequestError) as exc:
        await agent.load_session(cwd=PROJECT, session_id="nope")
    assert exc.value.code == -32002  # resource not found, as before
