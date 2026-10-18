"""A deferred JSONL session has no file until its first message (plan decision 17).

``JsonlSessionRepo.create({"deferred": True})`` makes a session that lives in memory. What is
recorded before the first ``message`` entry (a model change, a label, a leaf move) is held, and
the first message writes the header, the held entries and itself in one go.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from pi_agent_harness import (
    DeferredJsonlSessionStorage,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalExecutionEnv,
    MessageEntry,
    Session,
    SessionError,
    UserMessage,
)


def session_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.jsonl")) if directory.exists() else []


def file_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def make(directory: Path, **options: Any) -> Session:
    return await JsonlSessionRepo(directory).create({"deferred": True, "cwd": "/work", **options})


def hello(text: str = "hello") -> UserMessage:
    return UserMessage(content=text, timestamp=1)


def message_entry(entry_id: str, parent_id: str | None = None) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        parentId=parent_id,
        timestamp="2026-10-18T00:00:00.000Z",
        message={"role": "user", "content": "x", "timestamp": 1},
    )


@pytest.mark.asyncio
async def test_a_deferred_session_has_no_file_until_its_first_message(tmp_path: Path):
    repo = JsonlSessionRepo(tmp_path)
    session = await repo.create({"deferred": True, "id": "later", "cwd": "/work"})
    storage = session.get_storage()
    assert isinstance(storage, DeferredJsonlSessionStorage)
    assert not storage.written
    metadata = await session.get_metadata()

    # Nothing on disk, so nothing for the repo to find, list or open.
    assert session_files(tmp_path) == []
    assert await repo.list() == []
    assert await repo.find("later") is None
    assert not await repo.has_session("later")
    with pytest.raises(SessionError) as missing:
        await repo.open(metadata)
    assert missing.value.code == "not_found"

    await session.append_message(hello())

    assert storage.written
    (path,) = session_files(tmp_path)
    assert str(path) == metadata.path
    assert [m.id for m in await repo.list()] == ["later"]
    assert (await repo.find("later")) is not None
    assert await repo.has_session("later")


@pytest.mark.asyncio
async def test_what_comes_before_the_first_message_is_held_and_written_ahead_of_it(
    tmp_path: Path,
):
    session = await make(tmp_path, id="held")
    model_id = await session.append_model_change("mock", "mock-pro")
    thinking_id = await session.append_thinking_level_change("high")
    await session.append_session_name("my name")
    await session.move_to(thinking_id)
    assert session_files(tmp_path) == []

    # The held entries are in the session all the same: they can be read, and they have parents.
    assert len(await session.get_entries()) == 4
    assert (await session.build_context()).model == {"provider": "mock", "modelId": "mock-pro"}

    message_id = await session.append_message(hello())

    (path,) = session_files(tmp_path)
    header, *entries = file_lines(path)
    assert header["type"] == "session" and header["id"] == "held" and header["cwd"] == "/work"
    assert [e["type"] for e in entries] == [
        "model_change",
        "thinking_level_change",
        "session_info",
        "leaf",
        "message",
    ]
    assert entries[0]["id"] == model_id and entries[1]["parentId"] == model_id
    assert entries[-1]["id"] == message_id and entries[-1]["parentId"] == thinking_id

    # A later process reads it back as the session it was.
    reopened = Session(await JsonlSessionStorage.open_path(path))
    assert [e.id for e in await reopened.get_entries()] == [e["id"] for e in entries]
    assert await reopened.get_leaf_id() == message_id
    assert (await reopened.build_context()).model == {"provider": "mock", "modelId": "mock-pro"}


@pytest.mark.asyncio
async def test_a_session_that_only_held_entries_leaves_nothing(tmp_path: Path):
    session = await make(tmp_path)
    await session.append_model_change("mock", "mock-pro")
    await session.append_custom_entry("note", {"a": 1})
    assert session_files(tmp_path) == []


@pytest.mark.asyncio
async def test_after_the_first_message_each_entry_is_appended(tmp_path: Path):
    session = await make(tmp_path)
    first = await session.append_message(hello("one"))
    (path,) = session_files(tmp_path)
    assert len(file_lines(path)) == 2

    await session.append_label(first, "start")
    await session.append_message(hello("two"))
    await session.move_to(None)

    lines = file_lines(path)
    assert [line["type"] for line in lines] == ["session", "message", "label", "message", "leaf"]
    reopened = Session(await JsonlSessionStorage.open_path(path))
    assert [e.id for e in await reopened.get_entries()] == [line["id"] for line in lines[1:]]
    assert await reopened.get_leaf_id() is None
    assert await reopened.get_label(first) == "start"


@pytest.mark.asyncio
async def test_the_header_and_the_file_name_carry_the_time_the_session_was_made(tmp_path: Path):
    session = await make(tmp_path)
    made = (await session.get_metadata()).createdAt
    await asyncio.sleep(0.05)
    await session.append_message(hello())

    (path,) = session_files(tmp_path)
    assert file_lines(path)[0]["timestamp"] == made
    assert path.name.startswith(made.replace("-", "").replace(":", "").replace(".", ""))


@pytest.mark.asyncio
async def test_a_session_can_be_made_under_a_parent(tmp_path: Path):
    session = await make(tmp_path, parentSessionPath="/somewhere/parent.jsonl")
    await session.append_message(hello())
    (path,) = session_files(tmp_path)
    assert file_lines(path)[0]["parentSession"] == "/somewhere/parent.jsonl"


class FlakyEnv(LocalExecutionEnv):
    """Fails the next ``failures`` calls of ``write_file``, then works."""

    def __init__(self, cwd: Path, failures: int) -> None:
        super().__init__(cwd)
        self.failures = failures
        self.writes = 0

    async def write_file(self, path: str | Path, content: str | bytes) -> None:
        self.writes += 1
        if self.failures:
            self.failures -= 1
            raise OSError("disk full")
        await super().write_file(path, content)


@pytest.mark.asyncio
async def test_a_failed_write_records_nothing_and_the_next_message_writes_it_all(tmp_path: Path):
    env = FlakyEnv(tmp_path, failures=1)
    session = await JsonlSessionRepo(tmp_path, env).create({"deferred": True, "cwd": "/work"})
    model_id = await session.append_model_change("mock", "mock-pro")

    with pytest.raises(OSError, match="disk full"):
        await session.append_message(hello("lost"))
    assert session_files(tmp_path) == []
    assert [e.id for e in await session.get_entries()] == [model_id]  # the message is not in it

    await session.append_message(hello("kept"))

    (path,) = session_files(tmp_path)
    assert [line["type"] for line in file_lines(path)] == ["session", "model_change", "message"]
    assert file_lines(path)[2]["message"]["content"] == "kept"
    assert env.writes == 2  # one try that failed, one that held


@pytest.mark.asyncio
async def test_two_first_messages_at_once_write_the_file_once_and_keep_both(tmp_path: Path):
    session = await make(tmp_path)
    ids = await asyncio.gather(
        session.append_message(hello("a")), session.append_message(hello("b"))
    )

    (path,) = session_files(tmp_path)
    lines = file_lines(path)
    assert [line["type"] for line in lines] == ["session", "message", "message"]
    assert sorted(line["id"] for line in lines[1:]) == sorted(ids)


@pytest.mark.asyncio
async def test_an_entry_id_that_is_in_the_session_is_refused_before_and_after_the_write(
    tmp_path: Path,
):
    session = await make(tmp_path)
    storage = session.get_storage()
    await storage.append_entry(message_entry("one"))  # first message: the file is written
    with pytest.raises(SessionError) as after:
        await storage.append_entry(message_entry("one"))
    assert after.value.code == "invalid_entry"

    held = await make(tmp_path / "held")
    held_storage = held.get_storage()
    taken_id = await held.append_model_change("mock", "mock")
    with pytest.raises(SessionError) as before:
        await held_storage.append_entry(message_entry(taken_id))
    assert before.value.code == "invalid_entry"
    assert session_files(tmp_path / "held") == []  # a refused message does not write the file


@pytest.mark.asyncio
async def test_a_session_made_without_the_option_is_still_written_at_once(tmp_path: Path):
    session = await JsonlSessionRepo(tmp_path).create({"id": "eager", "cwd": "/work"})
    assert not isinstance(session.get_storage(), DeferredJsonlSessionStorage)
    (path,) = session_files(tmp_path)
    assert [line["type"] for line in file_lines(path)] == ["session"]
