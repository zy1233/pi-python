"""H1 tests for AgentHarness session tree and pi-v3 JSONL compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent_core.messages import AssistantMessage
from pi_agent_harness import (
    BranchSummaryEntry,
    ExecutionEnv,
    FileError,
    FileInfo,
    FileSystem,
    JsonlSessionMetadata,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalExecutionEnv,
    MemorySessionRepo,
    MemorySessionStorage,
    MessageEntry,
    Session,
    SessionError,
    SessionMetadata,
    Shell,
    UserMessage,
    build_session_context,
)
from pi_agent_harness.session.uuid7 import uuid7

FIXTURE = Path(__file__).parent / "fixtures" / "pi-v3-session.jsonl"
FIXTURE_TREE = Path(__file__).parent / "fixtures" / "pi-v3-session-tree.jsonl"


def _role(message):
    return getattr(message, "role", None) or message.get("role")


def _field(message, name: str):
    return getattr(message, name, None) if hasattr(message, name) else message.get(name)


def test_uuid7_is_time_ordered_canonical_uuid():
    first = uuid7()
    second = uuid7()

    # pi uses uuidv7's canonical hyphenated form (8-4-4-4-12).
    assert len(first) == 36
    assert [len(part) for part in first.split("-")] == [8, 4, 4, 4, 12]
    assert first[14] == "7"
    assert int(first.replace("-", ""), 16) >= 0
    assert first < second


@pytest.mark.asyncio
async def test_memory_session_builds_context_with_state_and_compaction():
    storage = await MemorySessionStorage.create(session_id="sess_mem")
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
async def test_jsonl_storage_replays_tree_fixture_with_compaction_and_abandoned_branch():
    session = Session(await JsonlSessionStorage.open_path(FIXTURE_TREE))

    # The label entry appended after the branch summary is the current leaf.
    assert await session.get_leaf_id() == "10000009"
    assert await session.get_label("10000008") == "alt-branch"
    # The abandoned branch stays on the tree even though it is off-path.
    assert await session.get_entry("10000006") is not None

    context = await session.build_context()
    assert [_role(m) for m in context.messages] == [
        "compactionSummary",
        "user",
        "assistant",
        "branchSummary",
    ]
    assert _field(context.messages[0], "summary") == "Earlier work summarized"
    assert _field(context.messages[0], "tokensBefore") == 4200
    assert context.messages[1].content[0]["text"] == "continue"
    assert context.messages[2].content[0]["text"] == "continuing"
    assert _field(context.messages[3], "summary") == "Abandoned: alternative attempt"
    assert _field(context.messages[3], "fromId") == "10000005"
    assert context.model == {"provider": "openai", "modelId": "gpt-4o-mini"}


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
    session = Session(await MemorySessionStorage.create(session_id="sess_mem"))

    with pytest.raises(SessionError) as exc:
        await session.move_to("missing")

    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_move_to_branch_summary_from_id_is_the_move_target():
    session = Session(await MemorySessionStorage.create(session_id="sess_mem"))
    first_id = await session.append_message(UserMessage(content="first", timestamp=1))
    await session.append_message(UserMessage(content="second", timestamp=2))

    # pi writes fromId = entryId ?? "root"; a summary-dict override is ignored.
    entry_id = await session.move_to(
        first_id, {"summary": "abandoned branch", "fromId": "override-attempt"}
    )
    entry = await session.get_entry(entry_id)
    assert isinstance(entry, BranchSummaryEntry)
    assert entry.fromId == first_id
    assert entry.parentId == first_id

    root_move_id = await session.move_to(None, {"summary": "back to root"})
    root_entry = await session.get_entry(root_move_id)
    assert isinstance(root_entry, BranchSummaryEntry)
    assert root_entry.fromId == "root"


@pytest.mark.asyncio
async def test_session_name_folds_newlines_only_and_blank_reads_back_as_none():
    session = Session(await MemorySessionStorage.create(session_id="sess_mem"))

    # pi only folds newline runs into spaces; tabs and doubled spaces survive.
    await session.append_session_name("  line1\r\nline2\ttab  spaces ")
    assert await session.get_session_name() == "line1 line2\ttab  spaces"

    # Whitespace-only names persist as "" and read back as absent, like pi's
    # `name?.trim() || undefined`.
    await session.append_session_name(" \r\n ")
    assert await session.get_session_name() is None


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
async def test_memory_repo_delete_is_idempotent():
    repo = MemorySessionRepo()
    session = await repo.create({"id": "victim"})
    metadata = await session.get_metadata()

    await repo.delete(metadata)
    await repo.delete(metadata)  # pi: plain Map.delete, no error on repeat
    await repo.delete(SessionMetadata(id="ghost", createdAt="2026-07-03T00:00:00.000Z"))

    assert await repo.list() == []


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


def _write_session_file(path: Path, session_id: str, timestamp: str) -> None:
    header = {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": timestamp,
        "cwd": "/workspace",
    }
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_jsonl_repo_list_skips_invalid_files_and_sorts_newest_first(tmp_path: Path):
    # Alphabetical file order is the reverse of createdAt order on purpose.
    _write_session_file(tmp_path / "a-old.jsonl", "old", "2026-07-03T00:00:01.000Z")
    _write_session_file(tmp_path / "b-new.jsonl", "new", "2026-07-03T00:00:02.000Z")
    (tmp_path / "c-broken.jsonl").write_text("not a session header\n", encoding="utf-8")
    (tmp_path / "d-empty.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    repo = JsonlSessionRepo(tmp_path)

    # pi: single invalid files are skipped, valid ones sort by createdAt desc.
    assert [m.id for m in await repo.list()] == ["new", "old"]


@pytest.mark.asyncio
async def test_jsonl_repo_delete_is_idempotent(tmp_path: Path):
    repo = JsonlSessionRepo(tmp_path)
    session = await repo.create({"id": "victim", "cwd": "/workspace"})
    metadata = await session.get_metadata()

    await repo.delete(metadata)
    await repo.delete(metadata)  # pi: remove with force semantics, no error
    await repo.delete(
        JsonlSessionMetadata(
            id="ghost",
            createdAt="2026-07-03T00:00:00.000Z",
            cwd="/workspace",
            path=str(tmp_path / "ghost.jsonl"),
        )
    )

    assert await repo.list() == []


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


class InMemoryJsonlRepoFs:
    """Pure in-memory JsonlRepoFs for injected-repo tests (no disk I/O)."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    def _norm(self, path: str | Path) -> str:
        return str(path).replace("\\", "/")

    async def read_text_file(self, path: str) -> str:
        key = self._norm(path)
        if key not in self._files:
            raise FileNotFoundError(key)
        return self._files[key]

    async def read_text_lines(self, path: str, max_lines: int | None = None) -> list[str]:
        lines = (await self.read_text_file(path)).splitlines()
        return lines[:max_lines] if max_lines is not None else lines

    async def write_file(self, path: str, content: str | bytes) -> None:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        self._files[self._norm(path)] = text

    async def append_file(self, path: str, content: str | bytes) -> None:
        key = self._norm(path)
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        self._files[key] = self._files.get(key, "") + text

    async def exists(self, path: str | Path) -> bool:
        return self._norm(path) in self._files

    async def remove(self, path: str | Path) -> None:
        # Force semantics per JsonlRepoFs: removing a missing path is a no-op.
        self._files.pop(self._norm(path), None)

    async def list_dir(self, path: str | Path) -> list[FileInfo]:
        dir_path = self._norm(path).rstrip("/")
        prefix = f"{dir_path}/"
        children: list[FileInfo] = []
        for file_path, content in self._files.items():
            if not file_path.startswith(prefix):
                continue
            rest = file_path[len(prefix) :]
            if "/" in rest:
                continue
            children.append(
                FileInfo(
                    name=rest,
                    path=rest,
                    kind="file",
                    size=len(content.encode("utf-8")),
                    mtimeMs=0.0,
                )
            )
        if not children:
            raise FileError("not_found", f"Directory not found: {path}", dir_path)
        return children


def test_local_execution_env_satisfies_runtime_protocols():
    env = LocalExecutionEnv("/tmp/workspace")
    assert isinstance(env, FileSystem)
    assert isinstance(env, Shell)
    assert isinstance(env, ExecutionEnv)


@pytest.mark.asyncio
async def test_jsonl_repo_in_memory_fs_end_to_end():
    fake = InMemoryJsonlRepoFs()
    repo = JsonlSessionRepo("/virtual/sessions", fs=fake)

    source = await repo.create({"id": "source", "cwd": "/workspace"})
    await source.append_message(UserMessage(content="first", timestamp=1))
    second_id = await source.append_message(UserMessage(content="second", timestamp=2))

    listed = await repo.list({"cwd": "/workspace"})
    assert [m.id for m in listed] == ["source"]
    assert all(path.startswith("/virtual/sessions/") for path in fake._files)

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
async def test_jsonl_repo_list_missing_directory_returns_empty(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    repo = JsonlSessionRepo(missing)
    assert await repo.list() == []
