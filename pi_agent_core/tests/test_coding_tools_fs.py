"""Tests for the pure-filesystem coding tools: read / write / ls."""

from __future__ import annotations

import base64
import json

import pytest

from pi_agent_core.coding_tools import create_ls_tool, create_read_tool, create_write_tool
from pi_agent_core.coding_tools.ls import LsParams
from pi_agent_core.coding_tools.read import ReadParams
from pi_agent_core.coding_tools.write import WriteParams
from pi_agent_core.validation import validate_tool_arguments

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Signal:
    def __init__(self, aborted: bool = False):
        self.aborted = aborted


def _text(result) -> str:
    assert result.content[0]["type"] == "text"
    return result.content[0]["text"]


# --- read ---


async def test_read_full_file(tmp_path):
    (tmp_path / "f.txt").write_text("hello\nworld", encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="f.txt"))
    assert _text(result) == "hello\nworld"
    assert result.details is None


async def test_read_offset_and_limit_with_more_lines_notice(tmp_path):
    content = "\n".join(f"line{i}" for i in range(1, 11))
    (tmp_path / "f.txt").write_text(content, encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="f.txt", offset=3, limit=2))
    assert _text(result) == "line3\nline4\n\n[6 more lines in file. Use offset=5 to continue.]"
    assert result.details is None  # user-limit stop is not a truncation


async def test_read_limit_reaching_eof_has_no_notice(tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc", encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="f.txt", offset=2, limit=99))
    assert _text(result) == "b\nc"


async def test_read_truncated_by_lines_notice_and_details(tmp_path):
    content = "\n".join(f"L{i}" for i in range(2500))
    (tmp_path / "big.txt").write_text(content, encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="big.txt"))
    text = _text(result)
    assert text.endswith("[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]")
    truncation = result.details["truncation"]
    assert truncation["truncated"] is True
    assert truncation["truncatedBy"] == "lines"
    assert truncation["outputLines"] == 2000
    json.dumps(result.details)  # details stay JSON-serializable


async def test_read_truncated_by_bytes_notice(tmp_path):
    content = "\n".join("x" * 100 for _ in range(1000))  # ~98KB, hits 50KB first
    (tmp_path / "big.txt").write_text(content, encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="big.txt"))
    assert "(50.0KB limit). Use offset=" in _text(result)
    assert result.details["truncation"]["truncatedBy"] == "bytes"


async def test_read_first_line_exceeds_limit_points_at_bash(tmp_path):
    (tmp_path / "big.txt").write_text("x" * (60 * 1024), encoding="utf-8")
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="big.txt"))
    assert _text(result) == (
        "[Line 1 is 60.0KB, exceeds 50.0KB limit. Use bash: sed -n '1p' big.txt | head -c 51200]"
    )
    assert result.details["truncation"]["firstLineExceedsLimit"] is True


async def test_read_offset_beyond_end_raises(tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc", encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"Offset 99 is beyond end of file \(3 lines total\)"):
        await tool.execute("t1", ReadParams(path="f.txt", offset=99))


async def test_read_missing_file_raises(tmp_path):
    tool = create_read_tool(str(tmp_path))
    with pytest.raises(OSError):
        await tool.execute("t1", ReadParams(path="nope.txt"))


async def test_read_image_returns_image_content(tmp_path):
    (tmp_path / "pic.png").write_bytes(PNG_BYTES)
    tool = create_read_tool(str(tmp_path))
    result = await tool.execute("t1", ReadParams(path="pic.png"))
    assert result.content[0] == {"type": "text", "text": "Read image file [image/png]"}
    image = result.content[1]
    assert image["type"] == "image"
    assert image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == PNG_BYTES


async def test_read_aborted_signal_raises(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8", newline="")
    tool = create_read_tool(str(tmp_path))
    with pytest.raises(RuntimeError, match="Operation aborted"):
        await tool.execute("t1", ReadParams(path="f.txt"), signal=_Signal(aborted=True))


# --- write ---


async def test_write_creates_parent_dirs_and_reports_bytes(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await tool.execute("t1", WriteParams(path="a/b/c.txt", content="hello"))
    assert _text(result) == "Successfully wrote 5 bytes to a/b/c.txt"
    assert result.details is None
    assert (tmp_path / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "hello"


async def test_write_overwrites_existing_file(tmp_path):
    (tmp_path / "f.txt").write_text("old content", encoding="utf-8", newline="")
    tool = create_write_tool(str(tmp_path))
    await tool.execute("t1", WriteParams(path="f.txt", content="new"))
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "new"


async def test_write_reports_utf8_byte_count(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await tool.execute("t1", WriteParams(path="f.txt", content="\u597d\u597d"))
    assert _text(result) == "Successfully wrote 6 bytes to f.txt"


async def test_write_preserves_lf_line_endings(tmp_path):
    tool = create_write_tool(str(tmp_path))
    await tool.execute("t1", WriteParams(path="f.txt", content="a\nb\n"))
    assert (tmp_path / "f.txt").read_bytes() == b"a\nb\n"


async def test_write_aborted_signal_raises_before_writing(tmp_path):
    tool = create_write_tool(str(tmp_path))
    with pytest.raises(RuntimeError, match="Operation aborted"):
        await tool.execute(
            "t1", WriteParams(path="f.txt", content="x"), signal=_Signal(aborted=True)
        )
    assert not (tmp_path / "f.txt").exists()


# --- ls ---


async def test_ls_sorts_suffixes_dirs_and_includes_dotfiles(tmp_path):
    (tmp_path / "B.txt").write_text("", encoding="utf-8", newline="")
    (tmp_path / "a.txt").write_text("", encoding="utf-8", newline="")
    (tmp_path / ".hidden").write_text("", encoding="utf-8", newline="")
    (tmp_path / "sub").mkdir()
    tool = create_ls_tool(str(tmp_path))
    result = await tool.execute("t1", LsParams())
    assert _text(result) == ".hidden\na.txt\nB.txt\nsub/"
    assert result.details is None


async def test_ls_empty_directory(tmp_path):
    tool = create_ls_tool(str(tmp_path))
    result = await tool.execute("t1", LsParams())
    assert _text(result) == "(empty directory)"


async def test_ls_relative_subdirectory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("", encoding="utf-8", newline="")
    tool = create_ls_tool(str(tmp_path))
    result = await tool.execute("t1", LsParams(path="sub"))
    assert _text(result) == "x.txt"


async def test_ls_entry_limit_notice_and_details(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (tmp_path / name).write_text("", encoding="utf-8", newline="")
    tool = create_ls_tool(str(tmp_path))
    result = await tool.execute("t1", LsParams(limit=2))
    assert _text(result) == "a.txt\nb.txt\n\n[2 entries limit reached. Use limit=4 for more]"
    assert result.details == {"entryLimitReached": 2}


async def test_ls_path_not_found_raises(tmp_path):
    tool = create_ls_tool(str(tmp_path))
    with pytest.raises(ValueError, match="Path not found:"):
        await tool.execute("t1", LsParams(path="missing"))


async def test_ls_not_a_directory_raises(tmp_path):
    (tmp_path / "f.txt").write_text("", encoding="utf-8", newline="")
    tool = create_ls_tool(str(tmp_path))
    with pytest.raises(ValueError, match="Not a directory:"):
        await tool.execute("t1", LsParams(path="f.txt"))


# --- protocol wiring ---


def test_tools_expose_agent_tool_protocol_surface(tmp_path):
    for factory, name in (
        (create_read_tool, "read"),
        (create_write_tool, "write"),
        (create_ls_tool, "ls"),
    ):
        tool = factory(str(tmp_path))
        assert tool.name == name
        assert tool.label == name
        assert tool.description
        assert tool.execution_mode is None
        assert tool.prepare_arguments is None


def test_validate_tool_arguments_produces_params_model(tmp_path):
    tool = create_read_tool(str(tmp_path))
    tool_call = {"id": "1", "name": "read", "arguments": {"path": "f.txt", "offset": 3}}
    params = validate_tool_arguments(tool, tool_call)
    assert isinstance(params, ReadParams)
    assert params.path == "f.txt"
    assert params.offset == 3
    assert params.limit is None


def test_invalid_arguments_fail_validation(tmp_path):
    tool = create_write_tool(str(tmp_path))
    tool_call = {"id": "1", "name": "write", "arguments": {"path": "f.txt"}}  # content missing
    with pytest.raises(Exception, match="content"):
        validate_tool_arguments(tool, tool_call)
