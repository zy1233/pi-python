"""Tests for coding_tools shared infrastructure (truncate/path_utils/mutation_queue)."""

from __future__ import annotations

import asyncio
import os

from pi_agent_core.coding_tools import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    detect_image_mime,
    format_size,
    glob_to_regex,
    resolve_to_cwd,
    truncate_head,
    truncate_line,
    truncate_tail,
    with_file_mutation_queue,
)
from pi_agent_core.coding_tools.path_utils import wsl_mnt_path_to_windows

# --- truncate: shared behavior ---


def test_no_truncation_returns_content_unchanged():
    content = "line1\nline2\nline3\n"
    result = truncate_head(content)
    assert result.content == content
    assert not result.truncated
    assert result.truncated_by is None
    assert result.total_lines == 3  # trailing newline does not add a line
    assert result.output_lines == 3
    assert result.max_lines == DEFAULT_MAX_LINES
    assert result.max_bytes == DEFAULT_MAX_BYTES


def test_empty_content_is_untouched():
    result = truncate_head("")
    assert result.content == ""
    assert not result.truncated
    assert result.total_lines == 0


def test_to_dict_uses_camel_case_keys():
    d = truncate_head("x\n" * 10, max_lines=2).to_dict()
    assert d["truncated"] is True
    assert d["truncatedBy"] == "lines"
    assert d["totalLines"] == 10
    assert d["outputLines"] == 2
    assert d["firstLineExceedsLimit"] is False
    assert d["lastLinePartial"] is False


# --- truncate_head ---


def test_head_line_limit_hit_first():
    content = "\n".join(f"line{i}" for i in range(10))
    result = truncate_head(content, max_lines=3, max_bytes=10_000)
    assert result.content == "line0\nline1\nline2"
    assert result.truncated
    assert result.truncated_by == "lines"
    assert result.output_lines == 3
    assert result.total_lines == 10


def test_head_byte_limit_hit_first():
    # Lines of 2 bytes; cumulative with joining newlines: 2, 5, 8, ...
    content = "aa\nbb\ncc\ndd"
    result = truncate_head(content, max_lines=100, max_bytes=5)
    assert result.content == "aa\nbb"
    assert result.truncated_by == "bytes"
    assert result.output_lines == 2
    assert result.output_bytes == 5


def test_head_never_returns_partial_line():
    result = truncate_head("aaaa\nbb", max_lines=100, max_bytes=6)
    # Second line (+newline) would exceed 6 bytes; it is dropped whole.
    assert result.content == "aaaa"
    assert result.truncated_by == "bytes"


def test_head_first_line_exceeds_byte_limit():
    content = "x" * 100 + "\nshort"
    result = truncate_head(content, max_bytes=50)
    assert result.content == ""
    assert result.truncated
    assert result.truncated_by == "bytes"
    assert result.first_line_exceeds_limit
    assert result.output_lines == 0
    assert result.output_bytes == 0


# --- truncate_tail ---


def test_tail_keeps_last_lines():
    content = "\n".join(f"line{i}" for i in range(10))
    result = truncate_tail(content, max_lines=2, max_bytes=10_000)
    assert result.content == "line8\nline9"
    assert result.truncated_by == "lines"
    assert result.output_lines == 2


def test_tail_byte_limit_keeps_whole_lines_from_end():
    content = "aa\nbb\ncc\ndd"
    result = truncate_tail(content, max_lines=100, max_bytes=5)
    assert result.content == "cc\ndd"
    assert result.truncated_by == "bytes"
    assert not result.last_line_partial


def test_tail_partial_last_line_when_single_line_too_big():
    content = "short\n" + "z" * 100
    result = truncate_tail(content, max_lines=100, max_bytes=10)
    assert result.content == "z" * 10
    assert result.truncated_by == "bytes"
    assert result.last_line_partial


def test_tail_partial_cut_lands_on_utf8_boundary():
    # Each CJK char is 3 UTF-8 bytes; a 10-byte budget cannot split a char.
    line = "好" * 10  # 30 bytes
    result = truncate_tail(line, max_bytes=10)
    assert result.content == "好" * 3  # 9 bytes, boundary-aligned
    assert result.last_line_partial
    result.content.encode("utf-8").decode("utf-8")  # round-trips cleanly


def test_head_multibyte_lines_count_utf8_bytes():
    # Each line is 6 bytes ("好好"); with newlines: 6, 13, 20, ...
    content = "好好\n好好\n好好"
    result = truncate_head(content, max_lines=100, max_bytes=13)
    assert result.content == "好好\n好好"
    assert result.output_bytes == 13


# --- truncate_line / format_size ---


def test_truncate_line_caps_and_flags():
    text, was_truncated = truncate_line("a" * 501, max_chars=500)
    assert text == "a" * 500 + "... [truncated]"
    assert was_truncated
    text, was_truncated = truncate_line("ok")
    assert text == "ok"
    assert not was_truncated


def test_format_size():
    assert format_size(512) == "512B"
    assert format_size(50 * 1024) == "50.0KB"
    assert format_size(int(1.5 * 1024 * 1024)) == "1.5MB"


# --- path_utils: resolve_to_cwd ---


def test_resolve_relative_joins_cwd(tmp_path):
    cwd = str(tmp_path)
    expected = os.path.normpath(os.path.join(cwd, "sub", "file.txt"))
    assert resolve_to_cwd("sub/file.txt", cwd) == expected


def test_resolve_absolute_passes_through(tmp_path):
    absolute = str(tmp_path / "x.txt")
    assert resolve_to_cwd(absolute, "/elsewhere") == os.path.normpath(absolute)


def test_wsl_mnt_path_to_windows():
    assert wsl_mnt_path_to_windows("/mnt/d/work/pi-python") == "D:/work/pi-python"
    assert wsl_mnt_path_to_windows("\\mnt\\c\\Users\\foo") == "C:/Users/foo"
    assert wsl_mnt_path_to_windows("D:/work/foo") is None


def test_resolve_wsl_cwd_on_windows(monkeypatch):
    monkeypatch.setattr("pi_agent_core.coding_tools.path_utils.sys.platform", "win32")
    assert resolve_to_cwd("pelican.svg", "/mnt/d/work/pi-python") == os.path.normpath(
        "D:/work/pi-python/pelican.svg"
    )
    assert resolve_to_cwd("/mnt/d/work/pelican.svg", "/mnt/d/work/pi-python") == os.path.normpath(
        "D:/work/pelican.svg"
    )


def test_resolve_expands_home():
    assert resolve_to_cwd("~", "/cwd") == os.path.normpath(os.path.expanduser("~"))


def test_resolve_normalizes_dotdot(tmp_path):
    cwd = str(tmp_path)
    assert resolve_to_cwd("a/../b.txt", cwd) == os.path.normpath(os.path.join(cwd, "b.txt"))


# --- path_utils: glob_to_regex ---


def test_glob_star_stays_within_segment():
    rx = glob_to_regex("*.py")
    assert rx.fullmatch("main.py")
    assert not rx.fullmatch("src/main.py")


def test_glob_doublestar_slash_matches_any_depth_including_none():
    rx = glob_to_regex("**/*.py")
    assert rx.fullmatch("main.py")
    assert rx.fullmatch("src/a/b/main.py")


def test_glob_path_pattern_with_inner_doublestar():
    rx = glob_to_regex("src/**/*.spec.ts")
    assert rx.fullmatch("src/x.spec.ts")
    assert rx.fullmatch("src/deep/nested/x.spec.ts")
    assert not rx.fullmatch("other/x.spec.ts")


def test_glob_question_mark_single_char():
    rx = glob_to_regex("?at.txt")
    assert rx.fullmatch("cat.txt")
    assert not rx.fullmatch("at.txt")
    assert not rx.fullmatch("chat.txt")


def test_glob_escapes_regex_metacharacters():
    rx = glob_to_regex("a+b.txt")
    assert rx.fullmatch("a+b.txt")
    assert not rx.fullmatch("aab.txt")


def test_glob_trailing_doublestar():
    rx = glob_to_regex("src/**")
    assert rx.fullmatch("src/a")
    assert rx.fullmatch("src/a/b/c.txt")


# --- path_utils: detect_image_mime ---


def test_detect_image_magic_numbers():
    assert detect_image_mime(b"\x89PNG\r\n\x1a\n" + b"rest") == "image/png"
    assert detect_image_mime(b"\xff\xd8\xff\xe0" + b"rest") == "image/jpeg"
    assert detect_image_mime(b"GIF89a" + b"rest") == "image/gif"
    assert detect_image_mime(b"RIFF\x00\x00\x00\x00WEBP" + b"rest") == "image/webp"
    assert detect_image_mime(b"BM" + b"rest") == "image/bmp"


def test_detect_image_rejects_non_images():
    assert detect_image_mime(b"hello world") is None
    assert detect_image_mime(b"") is None
    assert detect_image_mime(b"RIFF\x00\x00\x00\x00WAVE") is None  # RIFF but not WEBP


# --- mutation_queue ---


async def test_same_file_mutations_serialize(tmp_path):
    path = str(tmp_path / "f.txt")
    order: list[str] = []

    async def job(tag: str) -> None:
        async def run() -> None:
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-end")

        await with_file_mutation_queue(path, run)

    await asyncio.gather(job("a"), job("b"))
    assert order in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )


async def test_same_file_different_spellings_share_lock(tmp_path):
    # realpath collapses the redundant segment onto the same key.
    direct = str(tmp_path / "f.txt")
    indirect = str(tmp_path / "sub" / ".." / "f.txt")
    order: list[str] = []

    async def job(tag: str, path: str) -> None:
        async def run() -> None:
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-end")

        await with_file_mutation_queue(path, run)

    await asyncio.gather(job("a", direct), job("b", indirect))
    assert order[0].endswith("start")
    assert order[1] == order[0].replace("start", "end")


async def test_different_files_do_not_block_each_other(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_a() -> None:
        async def run() -> None:
            started.set()
            await release.wait()

        await with_file_mutation_queue(str(tmp_path / "a.txt"), run)

    async def poke_b() -> None:
        # Runs while a.txt's lock is held; deadlocks if locks were shared.
        async def run() -> None:
            release.set()

        await started.wait()
        await with_file_mutation_queue(str(tmp_path / "b.txt"), run)

    await asyncio.wait_for(asyncio.gather(hold_a(), poke_b()), timeout=2)


async def test_mutation_queue_returns_fn_result(tmp_path):
    async def run() -> str:
        return "value"

    assert await with_file_mutation_queue(str(tmp_path / "f.txt"), run) == "value"
