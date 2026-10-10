"""``JsonlSessionRepo.list_page``, ``find`` and ``has_session``: a listing a page at a time, a
lookup by id, a check that an id is free, and none of them reads more headers than it must."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent_harness import JsonlSessionRepo, LocalExecutionEnv
from pi_agent_harness.types import FileInfo

PROJECT = "/work/project"


class CountingEnv(LocalExecutionEnv):
    """A file system that remembers which files had their header read."""

    def __init__(self, cwd: str | Path) -> None:
        super().__init__(cwd)
        self.read: list[str] = []
        self.ghosts: list[str] = []

    async def read_text_lines(self, path, max_lines=None):
        self.read.append(Path(path).name)
        return await super().read_text_lines(path, max_lines)

    async def list_dir(self, path):
        listed = await super().list_dir(path)
        # Files that were there a moment ago (the listing and the read are not atomic).
        listed += [FileInfo(name=n, path=n, kind="file", size=0, mtimeMs=0) for n in self.ghosts]
        return sorted(listed, key=lambda info: info.name)


def write_session(
    directory: Path,
    session_id: str,
    created_at: str,
    cwd: str = PROJECT,
    *,
    name: str | None = None,
) -> str:
    """A session file named the way the repo names it (or ``name``); returns the file name."""
    safe = created_at.replace(":", "").replace("-", "").replace(".", "").replace("+", "")
    file_name = name or f"{safe}-{session_id}.jsonl"
    header = {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": created_at,
        "cwd": cwd,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file_name).write_text(json.dumps(header) + "\n", encoding="utf-8")
    return file_name


def stamp(minute: int) -> str:
    return f"2026-10-01T10:{minute:02d}:00.000Z"


@pytest.fixture
def sessions(tmp_path) -> Path:
    return tmp_path / "sessions"


def make_repo(sessions: Path) -> tuple[JsonlSessionRepo, CountingEnv]:
    env = CountingEnv(sessions.parent)
    return JsonlSessionRepo(sessions, env), env


async def walk(repo: JsonlSessionRepo, **options) -> list[list[str]]:
    """Every page, as lists of session ids."""
    pages: list[list[str]] = []
    after = None
    while True:
        page = await repo.list_page({**options, "after": after})
        pages.append([m.id for m in page.sessions])
        if page.next_after is None:
            return pages
        after = page.next_after


async def test_pages_cover_the_listing_newest_first(sessions):
    for minute in range(7):
        write_session(sessions, f"s{minute}", stamp(minute))
    repo, _ = make_repo(sessions)
    assert await walk(repo, limit=3) == [["s6", "s5", "s4"], ["s3", "s2", "s1"], ["s0"]]
    everything = [m.id for m in await repo.list()]
    assert [i for page in await walk(repo, limit=2) for i in page] == everything


async def test_a_page_has_a_cursor_only_when_another_session_follows(sessions):
    for minute in range(4):
        write_session(sessions, f"s{minute}", stamp(minute))
    repo, _ = make_repo(sessions)
    assert await walk(repo, limit=2) == [["s3", "s2"], ["s1", "s0"]]
    assert await walk(repo, limit=4) == [["s3", "s2", "s1", "s0"]]
    assert await walk(repo, limit=100) == [["s3", "s2", "s1", "s0"]]


async def test_pages_filter_by_directory_and_stay_full(sessions):
    for minute in range(9):
        write_session(
            sessions, f"s{minute}", stamp(minute), PROJECT if minute % 3 == 0 else "/else"
        )
    repo, _ = make_repo(sessions)
    assert await walk(repo, limit=2, cwd=PROJECT) == [["s6", "s3"], ["s0"]]
    assert await walk(repo, limit=5, cwd="/nowhere") == [[]]


async def test_a_page_reads_only_the_headers_it_needs(sessions):
    for minute in range(30):
        write_session(sessions, f"s{minute:02d}", stamp(minute))
    repo, env = make_repo(sessions)
    page = await repo.list_page({"limit": 3})
    assert [m.id for m in page.sessions] == ["s29", "s28", "s27"]
    assert len(env.read) == 4  # the page and a look at the next session, not all 30
    env.read.clear()
    nxt = await repo.list_page({"limit": 3, "after": page.next_after})
    assert [m.id for m in nxt.sessions] == ["s26", "s25", "s24"]
    assert len(env.read) == 4


async def test_a_session_created_after_the_first_page_does_not_disturb_the_next(sessions):
    for minute in range(1, 6):
        write_session(sessions, f"s{minute}", stamp(minute))
    repo, _ = make_repo(sessions)
    first = await repo.list_page({"limit": 2})
    write_session(sessions, "newest", stamp(50))  # newer than everything: before the cursor
    second = await repo.list_page({"limit": 2, "after": first.next_after})
    assert [m.id for m in second.sessions] == ["s3", "s2"]


async def test_a_deleted_session_does_not_break_the_cursor(sessions):
    names = {minute: write_session(sessions, f"s{minute}", stamp(minute)) for minute in range(6)}
    repo, _ = make_repo(sessions)
    first = await repo.list_page({"limit": 2})
    assert first.next_after == names[4]
    (sessions / names[4]).unlink()  # the cursor's own file
    second = await repo.list_page({"limit": 2, "after": first.next_after})
    assert [m.id for m in second.sessions] == ["s3", "s2"]


async def test_files_that_are_not_sessions_are_skipped(sessions):
    write_session(sessions, "s1", stamp(1))
    (sessions / "20261001T100200000Z-bad.jsonl").write_text("not json\n", encoding="utf-8")
    (sessions / "20261001T100300000Z-empty.jsonl").write_text("", encoding="utf-8")
    (sessions / "notes.txt").write_text("hi", encoding="utf-8")
    (sessions / "20261001T100400000Z-dir.jsonl").mkdir()
    write_session(sessions, "s5", stamp(5))
    repo, env = make_repo(sessions)
    env.ghosts.append("20261001T100600000Z-ghost.jsonl")  # listed, but gone when read
    assert await walk(repo, limit=10) == [["s5", "s1"]]
    assert await walk(repo, limit=1) == [["s5"], ["s1"]]


async def test_a_missing_directory_is_an_empty_page(tmp_path):
    repo = JsonlSessionRepo(tmp_path / "nowhere", CountingEnv(tmp_path))
    page = await repo.list_page({"limit": 5})
    assert (page.sessions, page.next_after) == ([], None)


@pytest.mark.parametrize("limit", [None, 0, -1, True, "5", 2.5])
async def test_the_limit_must_be_a_positive_integer(sessions, limit):
    repo, _ = make_repo(sessions)
    with pytest.raises(ValueError, match="limit"):
        await repo.list_page({} if limit is None else {"limit": limit})


async def test_a_cursor_that_matches_nothing_just_ends_the_listing(sessions):
    write_session(sessions, "s1", stamp(1))
    repo, _ = make_repo(sessions)
    for after in ["", "0", "zzzz", "../../etc/passwd"]:
        page = await repo.list_page({"limit": 5, "after": after})
        assert page.next_after is None
        assert [m.id for m in page.sessions] == (["s1"] if after > "20261001" else [])


async def test_find_reads_only_the_file_that_has_the_id(sessions):
    for minute in range(20):
        write_session(sessions, f"s{minute:02d}", stamp(minute))
    repo, env = make_repo(sessions)
    found = await repo.find("s07")
    assert found is not None and found.id == "s07" and found.cwd == PROJECT
    assert len(env.read) == 1
    assert found == next(m for m in await repo.list() if m.id == "s07")


async def test_find_tells_ids_that_end_alike_apart(sessions):
    write_session(sessions, "aaaa", stamp(1))
    write_session(sessions, "0192-aaaa", stamp(2))  # a uuid-like id that ends in the first one
    repo, _ = make_repo(sessions)
    assert (await repo.find("aaaa")).id == "aaaa"
    assert (await repo.find("0192-aaaa")).id == "0192-aaaa"


async def test_find_trusts_the_header_not_the_file_name(sessions):
    write_session(sessions, "other", stamp(1), name="20261001T100100000Z-wanted.jsonl")
    repo, _ = make_repo(sessions)
    assert await repo.find("wanted") is None
    assert (await repo.find("other")).id == "other"  # a foreign name is found the slow way


async def test_find_of_an_unknown_id(sessions):
    write_session(sessions, "s1", stamp(1))
    repo, _ = make_repo(sessions)
    for unknown in ["nope", "", "../s1", "*", "s1.jsonl"]:
        assert await repo.find(unknown) is None
    empty = JsonlSessionRepo(sessions.parent / "nowhere", CountingEnv(sessions.parent))
    assert await empty.find("s1") is None


async def test_has_session_opens_only_the_file_that_has_the_id(sessions):
    for minute in range(20):
        write_session(sessions, f"s{minute:02d}", stamp(minute))
    repo, env = make_repo(sessions)
    assert await repo.has_session("s07") is True
    assert len(env.read) == 1
    env.read.clear()
    assert await repo.has_session("free") is False
    assert env.read == []  # a free id costs the listing, not a read per session


async def test_has_session_opens_a_file_whose_name_does_not_say_whose_it_is(sessions):
    write_session(sessions, "s1", stamp(1))
    write_session(sessions, "copied", stamp(2), name="copied-by-hand.jsonl")
    repo, env = make_repo(sessions)
    assert await repo.has_session("copied") is True  # only its header knows
    assert env.read == ["copied-by-hand.jsonl"]
    env.read.clear()
    assert await repo.has_session("free") is False
    assert env.read == ["copied-by-hand.jsonl"]  # it might have been, so it is opened; s1's is not


async def test_has_session_tells_ids_that_end_alike_apart(sessions):
    write_session(sessions, "0192-aaaa", stamp(1))
    repo, env = make_repo(sessions)
    assert await repo.has_session("aaaa") is False
    assert env.read == []
    assert await repo.has_session("0192-aaaa") is True
    assert len(env.read) == 1


async def test_has_session_trusts_the_header_when_the_name_carries_the_id(sessions):
    write_session(sessions, "other", stamp(1), name="20261001T100100000Z-wanted.jsonl")
    repo, _ = make_repo(sessions)
    assert await repo.has_session("wanted") is False  # as `find`: the header says otherwise


async def test_has_session_takes_a_hand_renamed_file_for_what_its_name_says(sessions):
    # The one place it differs from `find`, which opens every file: that is what it exists to avoid.
    write_session(sessions, "other", stamp(1), name="20261001T100100000Z-wanted.jsonl")
    repo, _ = make_repo(sessions)
    assert await repo.find("other") is not None
    assert await repo.has_session("other") is False


async def test_has_session_passes_over_what_is_not_a_session(sessions):
    write_session(sessions, "s1", stamp(1))
    (sessions / "20261001T100200000Z-bad.jsonl").write_text("not json\n", encoding="utf-8")
    (sessions / "20261001T100300000Z-empty.jsonl").write_text("", encoding="utf-8")
    (sessions / "notes.txt").write_text("hi", encoding="utf-8")
    (sessions / "20261001T100400000Z-dir.jsonl").mkdir()
    repo, env = make_repo(sessions)
    env.ghosts.append("20261001T100600000Z-ghost.jsonl")  # listed, but gone when read
    assert await repo.has_session("s1") is True
    # Their names carry these ids, so they are opened; none of them is a session.
    for stray in ["bad", "empty", "dir", "ghost", "notes"]:
        assert await repo.has_session(stray) is False


async def test_has_session_of_a_missing_directory_is_false(tmp_path):
    repo = JsonlSessionRepo(tmp_path / "nowhere", CountingEnv(tmp_path))
    assert await repo.has_session("s1") is False
