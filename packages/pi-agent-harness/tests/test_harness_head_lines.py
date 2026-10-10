"""Reading the first lines of a file must not read the rest of it (a session list does this once per
session file, and a session file can be megabytes), and must give what reading it all would."""

from __future__ import annotations

import json

import pytest

from pi_agent_harness import JsonlSessionRepo
from pi_agent_harness.env import LocalExecutionEnv
from pi_agent_harness.session.jsonl_storage import (
    _PathJsonlStorageFs,
    load_jsonl_session_metadata,
)
from pi_agent_harness.text_lines import read_head_lines

HEADER = {"type": "session", "version": 3, "id": "s1", "timestamp": "2026-10-01T00:00:00.000Z"}

# Text is decoded a few KiB at a time, so a bad byte only goes unseen when it lies further on than
# that: "the rest of the file" in these tests is a long run of ordinary text and then one bad byte.
BODY_THEN_A_BAD_BYTE = b"x" * 200_000 + b"\xff"

# Bytes, not text: the cases that differ between line splitters are the line endings.
CONTENTS = {
    "empty": b"",
    "one line, no newline": b"a",
    "one line, newline": b"a\n",
    "lf": b"a\nb\nc\n",
    "lf, no final newline": b"a\nb\nc",
    "crlf": b"a\r\nb\r\nc\r\n",
    "cr": b"a\rb\rc\r",
    "mixed": b"a\r\nb\nc\rd",
    "blank lines": b"\n\na\n\nb\n",
    "only newlines": b"\n\n\n",
    "unicode separators": "a\u2028b\u2029c\x0bd\x0ce\x1cf\x85g\nh\n".encode(),
    "multibyte": "é\n日本語\n😀\n".encode(),
    "long line": b"x" * 100_000 + b"\ny\n",
}


@pytest.mark.parametrize("name", CONTENTS)
@pytest.mark.parametrize("max_lines", [None, 0, 1, 2, 3, 5, 100, -1, -2])
def test_head_lines_are_what_reading_everything_gives(tmp_path, name, max_lines):
    path = tmp_path / "f.txt"
    path.write_bytes(CONTENTS[name])
    everything = path.read_text(encoding="utf-8").splitlines()
    expected = everything if max_lines is None else everything[:max_lines]
    assert read_head_lines(path, max_lines) == expected


def test_head_lines_leave_the_rest_of_the_file_unread(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_bytes(json.dumps(HEADER).encode() + b"\n" + BODY_THEN_A_BAD_BYTE)
    with pytest.raises(UnicodeDecodeError):  # a full read does get to the bad byte
        path.read_text(encoding="utf-8")
    assert read_head_lines(path, 1) == [json.dumps(HEADER)]
    with pytest.raises(UnicodeDecodeError):  # and so does asking for every line
        read_head_lines(path, None)


def test_head_lines_of_a_missing_file_raise_like_a_read(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_head_lines(tmp_path / "nope.txt", 1)


async def test_local_execution_env_reads_head_lines(tmp_path):
    env = LocalExecutionEnv(tmp_path)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n" + BODY_THEN_A_BAD_BYTE)
    assert await env.read_text_lines("notes/a.txt", max_lines=2) == ["one", "two"]
    with pytest.raises(FileNotFoundError):
        await env.read_text_lines("notes/missing.txt", max_lines=1)


async def test_path_fs_reads_head_lines(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_bytes(b"first\nsecond\n" + BODY_THEN_A_BAD_BYTE)
    assert await _PathJsonlStorageFs().read_text_lines(str(path), max_lines=1) == ["first"]


async def test_session_metadata_needs_only_the_header(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_bytes(json.dumps({**HEADER, "cwd": "/work"}).encode() + b"\n" + BODY_THEN_A_BAD_BYTE)
    metadata = await load_jsonl_session_metadata(_PathJsonlStorageFs(), str(path))
    assert (metadata.id, metadata.cwd) == ("s1", "/work")


async def test_repo_lists_sessions_whose_bodies_are_damaged(tmp_path):
    """A listing is built from headers, so a damaged body does not stop it."""
    repo = JsonlSessionRepo(tmp_path)
    meta = await (await repo.create({"cwd": "/work"})).get_metadata()
    with open(meta.path, "ab") as handle:
        handle.write(BODY_THEN_A_BAD_BYTE)
    assert [m.id for m in await repo.list({"cwd": "/work"})] == [meta.id]
