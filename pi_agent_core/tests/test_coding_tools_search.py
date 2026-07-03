"""Tests for the search coding tools: grep (rg + fallback) and find."""

from __future__ import annotations

import shutil

import pytest

from pi_agent_core.coding_tools import create_find_tool, create_grep_tool
from pi_agent_core.coding_tools.find import FindParams
from pi_agent_core.coding_tools.grep import GrepParams
from pi_agent_core.coding_tools.path_utils import compile_glob

HAS_RG = shutil.which("rg") is not None


class _Signal:
    def __init__(self, aborted: bool = False):
        self.aborted = aborted


def _text(result) -> str:
    return result.content[0]["text"]


@pytest.fixture
def tree(tmp_path):
    """Small project tree shared by grep/find tests (LF content, no .gitignore)."""
    (tmp_path / "a.py").write_bytes(b"alpha\nBETA\ngamma\n")
    (tmp_path / "b.txt").write_bytes(b"alpha beta\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_bytes(b"beta gamma\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_bytes(b"beta hidden dep\n")
    return tmp_path


# --- compile_glob ---


def test_compile_glob_basename_pattern():
    regex, matches_path = compile_glob("*.py")
    assert matches_path is False
    assert regex.fullmatch("a.py")
    assert not regex.fullmatch("a.pyc")


def test_compile_glob_path_pattern_gets_any_depth_prefix():
    regex, matches_path = compile_glob("src/*.py")
    assert matches_path is True
    assert regex.fullmatch("src/a.py")
    assert regex.fullmatch("deep/src/a.py")
    assert not regex.fullmatch("src/nested/a.py")


def test_compile_glob_anchored_patterns():
    regex, _ = compile_glob("/src/*.py")
    assert regex.fullmatch("src/a.py")
    assert not regex.fullmatch("deep/src/a.py")
    regex, _ = compile_glob("**/*.py")
    assert regex.fullmatch("a.py")
    assert regex.fullmatch("x/y/a.py")


# --- grep (fallback path, deterministic) ---


async def test_grep_fallback_basic_output_format(tree):
    tool = create_grep_tool(str(tree), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="beta"))
    assert _text(result) == "b.txt:1: alpha beta\nsub/c.py:1: beta gamma"
    assert result.details is None


async def test_grep_fallback_ignore_case(tree):
    tool = create_grep_tool(str(tree), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="beta", ignoreCase=True))
    assert _text(result) == "a.py:2: BETA\nb.txt:1: alpha beta\nsub/c.py:1: beta gamma"


async def test_grep_fallback_literal_vs_regex(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"aXb\na.b\n")
    tool = create_grep_tool(str(tmp_path), use_fallback=True)
    regex_result = await tool.execute("t1", GrepParams(pattern="a.b"))
    assert _text(regex_result) == "f.txt:1: aXb\nf.txt:2: a.b"
    literal_result = await tool.execute("t1", GrepParams(pattern="a.b", literal=True))
    assert _text(literal_result) == "f.txt:2: a.b"


async def test_grep_fallback_context_block(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"l1\nl2\nMATCH\nl4\nl5\n")
    tool = create_grep_tool(str(tmp_path), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="MATCH", context=1))
    assert _text(result) == "f.txt-2- l2\nf.txt:3: MATCH\nf.txt-4- l4"


async def test_grep_fallback_limit_notice_and_details(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hit\nhit\nhit\nhit\nhit\n")
    tool = create_grep_tool(str(tmp_path), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="hit", limit=2))
    assert _text(result) == (
        "f.txt:1: hit\nf.txt:2: hit"
        "\n\n[2 matches limit reached. Use limit=4 for more, or refine pattern]"
    )
    assert result.details == {"matchLimitReached": 2}


async def test_grep_fallback_no_matches(tree):
    tool = create_grep_tool(str(tree), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="zzz_nothing"))
    assert _text(result) == "No matches found"
    assert result.details is None


async def test_grep_fallback_long_line_truncated_with_notice(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"needle " + b"x" * 600 + b"\n")
    tool = create_grep_tool(str(tmp_path), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="needle"))
    text = _text(result)
    assert "... [truncated]" in text
    assert text.endswith("[Some lines truncated to 500 chars. Use read tool to see full lines]")
    assert result.details == {"linesTruncated": True}


async def test_grep_fallback_prunes_ignored_dirs_and_binaries(tree):
    (tree / "bin.dat").write_bytes(b"beta\x00binary")
    tool = create_grep_tool(str(tree), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="beta"))
    text = _text(result)
    assert "node_modules" not in text
    assert "bin.dat" not in text


async def test_grep_fallback_glob_filter(tree):
    tool = create_grep_tool(str(tree), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="beta", glob="*.py"))
    assert _text(result) == "sub/c.py:1: beta gamma"


async def test_grep_fallback_single_file_uses_basename(tree):
    tool = create_grep_tool(str(tree), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="BETA", path="a.py"))
    assert _text(result) == "a.py:2: BETA"


async def test_grep_fallback_byte_truncation_notice(tmp_path):
    line = "needle " + "y" * 100
    (tmp_path / "f.txt").write_text(("\n".join([line] * 1000)) + "\n", encoding="utf-8")
    tool = create_grep_tool(str(tmp_path), use_fallback=True)
    result = await tool.execute("t1", GrepParams(pattern="needle", limit=2000))
    assert "[50.0KB limit reached]" in _text(result)
    assert result.details["truncation"]["truncatedBy"] == "bytes"


async def test_grep_path_not_found(tmp_path):
    tool = create_grep_tool(str(tmp_path), use_fallback=True)
    with pytest.raises(ValueError, match="Path not found:"):
        await tool.execute("t1", GrepParams(pattern="x", path="missing"))


async def test_grep_aborted_signal_raises(tree):
    tool = create_grep_tool(str(tree), use_fallback=True)
    with pytest.raises(RuntimeError, match="Operation aborted"):
        await tool.execute("t1", GrepParams(pattern="beta"), signal=_Signal(aborted=True))


# --- grep (ripgrep path, parity with fallback where deterministic) ---


@pytest.mark.skipif(not HAS_RG, reason="rg not on PATH")
async def test_grep_rg_basic_output_format(tree):
    tool = create_grep_tool(str(tree))
    result = await tool.execute("t1", GrepParams(pattern="BETA"))
    assert _text(result) == "a.py:2: BETA"
    assert result.details is None


@pytest.mark.skipif(not HAS_RG, reason="rg not on PATH")
async def test_grep_rg_glob_and_context(tree):
    tool = create_grep_tool(str(tree))
    result = await tool.execute("t1", GrepParams(pattern="gamma", glob="*.py", context=1))
    text = _text(result)
    assert "a.py-2- BETA" in text
    assert "a.py:3: gamma" in text
    assert "sub/c.py:1: beta gamma" in text
    assert "b.txt" not in text


@pytest.mark.skipif(not HAS_RG, reason="rg not on PATH")
async def test_grep_rg_limit_notice(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hit\nhit\nhit\nhit\n")
    tool = create_grep_tool(str(tmp_path))
    result = await tool.execute("t1", GrepParams(pattern="hit", limit=2))
    assert _text(result).endswith(
        "[2 matches limit reached. Use limit=4 for more, or refine pattern]"
    )
    assert result.details == {"matchLimitReached": 2}


@pytest.mark.skipif(not HAS_RG, reason="rg not on PATH")
async def test_grep_rg_no_matches(tree):
    tool = create_grep_tool(str(tree))
    result = await tool.execute("t1", GrepParams(pattern="zzz_nothing"))
    assert _text(result) == "No matches found"


# --- find ---


async def test_find_basename_pattern_matches_any_depth(tree):
    tool = create_find_tool(str(tree))
    result = await tool.execute("t1", FindParams(pattern="*.py"))
    assert _text(result) == "a.py\nsub/c.py"
    assert result.details is None


async def test_find_path_pattern_auto_prefixed(tree):
    (tree / "deep").mkdir()
    (tree / "deep" / "sub").mkdir()
    (tree / "deep" / "sub" / "d.py").write_bytes(b"")
    tool = create_find_tool(str(tree))
    result = await tool.execute("t1", FindParams(pattern="sub/*.py"))
    assert _text(result) == "deep/sub/d.py\nsub/c.py"


async def test_find_matches_directories_too(tree):
    tool = create_find_tool(str(tree))
    result = await tool.execute("t1", FindParams(pattern="sub"))
    assert _text(result) == "sub"


async def test_find_includes_dotfiles(tmp_path):
    (tmp_path / ".hidden.py").write_bytes(b"")
    tool = create_find_tool(str(tmp_path))
    result = await tool.execute("t1", FindParams(pattern="*.py"))
    assert _text(result) == ".hidden.py"


async def test_find_prunes_ignored_dirs(tree):
    tool = create_find_tool(str(tree))
    result = await tool.execute("t1", FindParams(pattern="x.py"))
    assert _text(result) == "No files found matching pattern"


async def test_find_limit_notice_and_details(tmp_path):
    for name in ("a.py", "b.py", "c.py", "d.py"):
        (tmp_path / name).write_bytes(b"")
    tool = create_find_tool(str(tmp_path))
    result = await tool.execute("t1", FindParams(pattern="*.py", limit=2))
    assert _text(result) == (
        "a.py\nb.py\n\n[2 results limit reached. Use limit=4 for more, or refine pattern]"
    )
    assert result.details == {"resultLimitReached": 2}


async def test_find_no_matches(tree):
    tool = create_find_tool(str(tree))
    result = await tool.execute("t1", FindParams(pattern="*.rs"))
    assert _text(result) == "No files found matching pattern"
    assert result.details is None


async def test_find_path_not_found(tmp_path):
    tool = create_find_tool(str(tmp_path))
    with pytest.raises(ValueError, match="Path not found:"):
        await tool.execute("t1", FindParams(pattern="*.py", path="missing"))


async def test_find_aborted_signal_raises(tree):
    tool = create_find_tool(str(tree))
    with pytest.raises(RuntimeError, match="Operation aborted"):
        await tool.execute("t1", FindParams(pattern="*.py"), signal=_Signal(aborted=True))


# --- protocol wiring ---


def test_search_tools_expose_agent_tool_protocol_surface(tmp_path):
    for factory, name in ((create_grep_tool, "grep"), (create_find_tool, "find")):
        tool = factory(str(tmp_path))
        assert tool.name == name
        assert tool.label == name
        assert tool.description
        assert tool.execution_mode is None
        assert tool.prepare_arguments is None
