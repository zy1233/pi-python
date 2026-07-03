"""H1 tests for AgentHarness session tree and pi-v3 JSONL compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent_core.messages import AssistantMessage
from pi_agent_harness import (
    JsonlSessionRepo,
    JsonlSessionStorage,
    MemorySessionRepo,
    MemorySessionStorage,
    MessageEntry,
    Session,
    SessionError,
    UserMessage,
    build_session_context,
)
from pi_agent_harness.session.uuid7 import uuid7

FIXTURE = Path(__file__).parent / "fixtures" / "pi-v3-session.jsonl"


def _role(message):
    return getattr(message, "role", None) or message.get("role")


def _field(message, name: str):
    return getattr(message, name, None) if hasattr(message, name) else message.get(name)


def test_uuid7_is_time_ordered_and_hexish():
    first = uuid7()
    second = uuid7()

    assert len(first) == 32
    assert int(first, 16) >= 0
    assert first < second


@pytest.mark.asyncio
async def test_memory_session_builds_context_with_state_and_compaction():
    storage = await MemorySessionStorage.create(cwd="/workspace", session_id="sess_mem")
    session = Session(storage)

    user1 = UserMessage(content="old request", timestamp=1)
    user2 = UserMessage(content="kept request", timestamp=2)
    assistant = AssistantMessage(
        content=[{"type": "text", "text": "answer"}],
        provider="anthropic",
        model="claude-3-5",
        timestamp=3,
    )

    await session.append_thinking_level_change("medium")
    await session.append_model_change("openai", "gpt-4o-mini")
    await session.append_active_tools_change(["read", "edit"])
    await session.append_message(user1)
    first_kept_id = await session.append_message(user2)
    await session.append_compaction("summary", first_kept_id, 123)
    await session.append_message(assistant)

    context = await session.build_context()

    assert context.thinkingLevel == "medium"
    # Assistant messages on the active branch restore the actual model used.
    assert context.model == {"provider": "anthropic", "modelId": "claude-3-5"}
    assert context.activeToolNames == ["read", "edit"]
    assert [_role(m) for m in context.messages] == [
        "compactionSummary",
        "user",
        "assistant",
    ]
    assert _field(context.messages[0], "summary") == "summary"
    assert context.messages[1].content == "kept request"


@pytest.mark.asyncio
async def test_jsonl_storage_reads_pi_v3_fixture_and_preserves_extra_fields():
    storage = await JsonlSessionStorage.open_path(FIXTURE)
    session = Session(storage)

    metadata = await session.get_metadata()
    assert metadata.id == "sess_fixture"
    assert metadata.cwd == "/workspace"
    assert metadata.parentSessionPath == "/parent/session.jsonl"
    assert await session.get_leaf_id() == "00000001"
    assert await session.get_label("00000001") == "start"

    entry = await session.get_entry("00000001")
    assert isinstance(entry, MessageEntry)
    assert entry.model_dump(exclude_none=True)["futureField"] == "kept"

    context = await session.build_context()
    assert len(context.messages) == 1
    assert context.messages[0].role == "user"
    assert context.messages[0].content[0]["text"] == "Hello from pi"


@pytest.mark.asyncio
async def test_jsonl_storage_round_trips_and_keeps_append_only_leaf(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    storage = await JsonlSessionStorage.create_path(path, cwd="/workspace", session_id="sess_json")
    session = Session(storage)

    first_id = await session.append_message(UserMessage(content="first", timestamp=10))
    second_id = await session.append_message(UserMessage(content="second", timestamp=20))
    await session.move_to(first_id)

    reopened = Session(await JsonlSessionStorage.open_path(path))
    entries = await reopened.get_entries()
    assert await reopened.get_leaf_id() == first_id
    assert [entry.id for entry in entries[:2]] == [first_id, second_id]
    assert entries[-1].type == "leaf"
    assert [m.content for m in (await reopened.build_context()).messages] == ["first"]

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "session"
    assert lines[-1]["type"] == "leaf"
    assert lines[-1]["targetId"] == first_id


@pytest.mark.asyncio
async def test_session_rejects_move_to_missing_entry():
    session = Session(await MemorySessionStorage.create(cwd="/workspace", session_id="sess_mem"))

    with pytest.raises(SessionError) as exc:
        await session.move_to("missing")

    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_memory_repo_fork_matches_pi_semantics():
    repo = MemorySessionRepo()
    source = await repo.create({"id": "source"})
    first_id = await source.append_message(UserMessage(content="first", timestamp=1))
    second_id = await source.append_message(UserMessage(content="second", timestamp=2))
    assistant_id = await source.append_message(
        AssistantMessage(content=[{"type": "text", "text": "answer"}], timestamp=3)
    )
    await source.move_to(first_id)
    metadata = await source.get_metadata()

    fork_at = await repo.fork(metadata, {"id": "fork-at", "entryId": second_id, "position": "at"})
    assert [m.content for m in (await fork_at.build_context()).messages] == [
        "first",
        "second",
    ]

    # Default position is "before" (edit-and-resend): path up to the parent.
    fork_default = await repo.fork(metadata, {"id": "fork-default", "entryId": second_id})
    assert [m.content for m in (await fork_default.build_context()).messages] == ["first"]

    # "before" requires the target to be a user message.
    with pytest.raises(SessionError) as exc:
        await repo.fork(metadata, {"id": "fork-bad", "entryId": assistant_id})
    assert exc.value.code == "invalid_fork_target"

    # No entryId: the whole tree is copied, including the abandoned branch and
    # the persisted leaf position.
    fork_all = await repo.fork(metadata, {"id": "fork-all"})
    assert len(await fork_all.get_entries()) == len(await source.get_entries())
    assert await fork_all.get_leaf_id() == first_id
    assert await fork_all.get_entry(assistant_id) is not None


@pytest.mark.asyncio
async def test_jsonl_repo_create_list_open_delete_and_fork(tmp_path: Path):
    repo = JsonlSessionRepo(tmp_path)
    source = await repo.create({"id": "source", "cwd": "/workspace"})
    await source.append_message(UserMessage(content="first", timestamp=1))
    second_id = await source.append_message(UserMessage(content="second", timestamp=2))

    listed = await repo.list({"cwd": "/workspace"})
    assert [m.id for m in listed] == ["source"]

    fork = await repo.fork(
        listed[0], {"id": "fork", "entryId": second_id, "position": "at", "cwd": "/workspace"}
    )
    assert [m.content for m in (await fork.build_context()).messages] == ["first", "second"]
    fork_metadata = await fork.get_metadata()
    assert fork_metadata.parentSessionPath == listed[0].path

    fork_before = await repo.fork(
        listed[0], {"id": "fork-b", "entryId": second_id, "cwd": "/workspace"}
    )
    assert [m.content for m in (await fork_before.build_context()).messages] == ["first"]

    reopened = await repo.open(fork_metadata)
    assert [m.content for m in (await reopened.build_context()).messages] == ["first", "second"]

    await repo.delete(fork_metadata)
    assert sorted(m.id for m in await repo.list()) == ["fork-b", "source"]


@pytest.mark.asyncio
async def test_jsonl_storage_tolerates_unknown_entry_types(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "version": 3,
                        "id": "sess_future",
                        "timestamp": "2026-07-03T00:00:00.000Z",
                        "cwd": "/workspace",
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "00000001",
                        "parentId": None,
                        "timestamp": "2026-07-03T00:00:01.000Z",
                        "message": {"role": "user", "content": "hi", "timestamp": 1},
                    }
                ),
                json.dumps(
                    {
                        "type": "future_entry",
                        "id": "00000002",
                        "parentId": "00000001",
                        "timestamp": "2026-07-03T00:00:02.000Z",
                        "payload": {"nested": True},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = Session(await JsonlSessionStorage.open_path(path))

    # The unknown entry stays on the tree (it is the current leaf and a valid
    # parent link) but is ignored during replay, matching pi's tolerance.
    assert await session.get_leaf_id() == "00000002"
    unknown = await session.get_entry("00000002")
    assert unknown is not None
    assert unknown.model_dump(exclude_none=True)["payload"] == {"nested": True}
    assert [m.content for m in (await session.build_context()).messages] == ["hi"]

    # Appending after it keeps the chain intact.
    await session.append_message(UserMessage(content="again", timestamp=2))
    reopened = Session(await JsonlSessionStorage.open_path(path))
    assert [m.content for m in (await reopened.build_context()).messages] == ["hi", "again"]
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[3]["parentId"] == "00000002"

    # Re-serializing the tolerated entry (e.g. during fork) keeps the foreign
    # fields and pi's base key order.
    copy = await JsonlSessionStorage.create_path(
        path.with_name("copy.jsonl"), cwd="/workspace", session_id="sess_copy"
    )
    await copy.append_entry(unknown.model_copy(deep=True))
    copied = json.loads(path.with_name("copy.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert list(copied.keys()) == ["type", "id", "parentId", "timestamp", "payload"]
    assert copied["payload"] == {"nested": True}


@pytest.mark.asyncio
async def test_jsonl_entry_key_order_matches_pi(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    storage = await JsonlSessionStorage.create_path(path, cwd="/w", session_id="sess_order")
    session = Session(storage)
    first_id = await session.append_message(UserMessage(content="hi", timestamp=1))
    await session.move_to(None)

    lines = path.read_text(encoding="utf-8").splitlines()
    root_keys = list(json.loads(lines[1]).keys())
    leaf_keys = list(json.loads(lines[2]).keys())

    # pi writes entry literals as {type, id, parentId, timestamp, ...}; the
    # null parentId of root entries and null leaf targetId must not be
    # reordered to the end of the line.
    assert root_keys == ["type", "id", "parentId", "timestamp", "message"]
    assert leaf_keys == ["type", "id", "parentId", "timestamp", "targetId"]
    assert json.loads(lines[1])["parentId"] is None
    assert json.loads(lines[2])["targetId"] is None
    assert json.loads(lines[2])["parentId"] == first_id


def test_build_session_context_accepts_raw_entries_from_fixture():
    entries = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines()[1:]:
        entries.append(JsonlSessionStorage.parse_entry_line(line, str(FIXTURE), len(entries) + 2))

    context = build_session_context(entries[:2])

    assert context.messages[0].role == "user"
    assert context.messages[1].role == "assistant"
    assert context.model == {"provider": "openai", "modelId": "gpt-4o-mini"}
