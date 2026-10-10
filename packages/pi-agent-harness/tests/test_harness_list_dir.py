"""``LocalExecutionEnv.list_dir`` is ``file_info`` of every child, only faster."""

from __future__ import annotations

import os
import sys

import pytest

from pi_agent_harness.env import LocalExecutionEnv
from pi_agent_harness.types import FileError


def _symlink(target, link, *, directory: bool = False) -> bool:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):  # Windows without the privilege
        return False
    return True


async def _one_by_one(env: LocalExecutionEnv, directory):
    return [
        await env.file_info(child) for child in sorted(directory.iterdir(), key=lambda p: p.name)
    ]


async def test_list_dir_gives_what_file_info_gives(tmp_path):
    work = tmp_path / "work"
    (work / "sessions" / "sub").mkdir(parents=True)
    (work / "sessions" / "b.jsonl").write_text("b")
    (work / "sessions" / "a.jsonl").write_text("aaaa")
    (work / "sessions" / "sub" / "c.txt").write_text("c")
    _symlink(work / "sessions" / "a.jsonl", work / "sessions" / "link-to-file")
    _symlink(work / "sessions" / "sub", work / "sessions" / "link-to-dir", directory=True)
    _symlink(work / "sessions" / "gone", work / "sessions" / "broken-link")

    env = LocalExecutionEnv(work)
    expected = await _one_by_one(env, work / "sessions")
    assert [info.name for info in expected] == sorted(info.name for info in expected)
    assert await env.list_dir("sessions") == expected
    assert await env.list_dir(str(work / "sessions")) == expected
    assert {info.kind for info in expected} >= {"file", "directory"}


async def test_list_dir_of_a_directory_outside_the_working_directory(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.txt").write_text("x")
    (tmp_path / "cwd").mkdir()
    env = LocalExecutionEnv(tmp_path / "cwd")
    listed = await env.list_dir(str(tmp_path / "elsewhere"))
    assert listed == await _one_by_one(env, tmp_path / "elsewhere")
    assert listed[0].path.endswith("/elsewhere/x.txt")  # not relative to the working directory


async def test_list_dir_of_an_empty_directory(tmp_path):
    (tmp_path / "empty").mkdir()
    assert await LocalExecutionEnv(tmp_path).list_dir("empty") == []


async def test_list_dir_errors_are_unchanged(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    env = LocalExecutionEnv(tmp_path)
    with pytest.raises(FileError) as missing:
        await env.list_dir("nope")
    assert missing.value.code == "not_found"
    with pytest.raises(FileError) as not_dir:
        await env.list_dir("file.txt")
    assert not_dir.value.code == "not_directory"


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes")
async def test_list_dir_refuses_what_file_info_refuses(tmp_path):
    (tmp_path / "d").mkdir()
    os.mkfifo(tmp_path / "d" / "pipe")
    env = LocalExecutionEnv(tmp_path)
    with pytest.raises(FileError) as one:
        await env.file_info("d/pipe")
    with pytest.raises(FileError) as many:
        await env.list_dir("d")
    assert (many.value.code, one.value.code) == ("invalid", "invalid")
