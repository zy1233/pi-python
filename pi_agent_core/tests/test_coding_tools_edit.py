"""Tests for the edit coding tool (matching, overlap, fuzzy, encoding round-trip)."""

from __future__ import annotations

import json

import pytest

from pi_agent_core.coding_tools.edit import (
    EditParams,
    create_edit_tool,
    prepare_edit_arguments,
)
from pi_agent_core.validation import validate_tool_arguments


class _Signal:
    def __init__(self, aborted: bool = False):
        self.aborted = aborted


def _params(path: str, *edits: tuple[str, str]) -> EditParams:
    return EditParams(path=path, edits=[{"oldText": old, "newText": new} for old, new in edits])


def _text(result) -> str:
    return result.content[0]["text"]


# --- basic replacement ---


async def test_edit_single_replacement(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello world\n")
    tool = create_edit_tool(str(tmp_path))
    result = await tool.execute("t1", _params("f.txt", ("world", "python")))
    assert _text(result) == "Successfully replaced 1 block(s) in f.txt."
    assert (tmp_path / "f.txt").read_bytes() == b"hello python\n"


async def test_edit_multiple_disjoint_edits_matched_against_original(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"aaa\nbbb\nccc\n")
    tool = create_edit_tool(str(tmp_path))
    result = await tool.execute("t1", _params("f.txt", ("ccc", "yyy"), ("aaa", "xxx")))
    assert _text(result) == "Successfully replaced 2 block(s) in f.txt."
    assert (tmp_path / "f.txt").read_bytes() == b"xxx\nbbb\nyyy\n"


async def test_edit_details_contain_diff_patch_and_first_changed_line(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"line1\nline2\nline3\nline4\nline5\n")
    tool = create_edit_tool(str(tmp_path))
    result = await tool.execute("t1", _params("f.txt", ("line3", "LINE3")))
    details = result.details
    assert details["diff"] == details["patch"]
    assert "@@" in details["patch"]
    assert "-line3" in details["patch"]
    assert "+LINE3" in details["patch"]
    assert details["firstChangedLine"] == 3
    json.dumps(details)  # stays JSON-serializable


# --- matching errors (pi wording) ---


async def test_edit_not_found_single_form(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"Could not find the exact text in f\.txt\."):
        await tool.execute("t1", _params("f.txt", ("nope", "x")))


async def test_edit_not_found_multi_form_reports_index(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"Could not find edits\[1\] in f\.txt\."):
        await tool.execute("t1", _params("f.txt", ("hello", "hi"), ("nope", "x")))


async def test_edit_duplicate_match_rejected(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"dup\ndup\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"Found 2 occurrences of the text in f\.txt\."):
        await tool.execute("t1", _params("f.txt", ("dup", "x")))


async def test_edit_overlapping_edits_rejected(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"abcdef\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"edits\[0\] and edits\[1\] overlap in f\.txt\."):
        await tool.execute("t1", _params("f.txt", ("abcd", "X"), ("cdef", "Y")))


async def test_edit_no_change_rejected(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"same\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"No changes made to f\.txt\."):
        await tool.execute("t1", _params("f.txt", ("same", "same")))


async def test_edit_empty_old_text_rejected(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"x\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"oldText must not be empty in f\.txt\."):
        await tool.execute("t1", _params("f.txt", ("", "y")))


async def test_edit_empty_edits_list_rejected(tmp_path):
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match="edits must contain at least one replacement"):
        await tool.execute("t1", EditParams(path="f.txt", edits=[]))


async def test_edit_missing_file_reports_error_code(tmp_path):
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(ValueError, match=r"Could not edit file: nope\.txt\. Error code: ENOENT\."):
        await tool.execute("t1", _params("nope.txt", ("a", "b")))


# --- encoding round-trip ---


async def test_edit_crlf_file_round_trips(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
    tool = create_edit_tool(str(tmp_path))
    # The model sees LF text, so multi-line oldText uses "\n".
    await tool.execute("t1", _params("f.txt", ("alpha\nbeta", "alpha\nBETA")))
    assert (tmp_path / "f.txt").read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


async def test_edit_bom_round_trips(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"\xef\xbb\xbfhello\n")
    tool = create_edit_tool(str(tmp_path))
    await tool.execute("t1", _params("f.txt", ("hello", "bye")))
    assert (tmp_path / "f.txt").read_bytes() == b"\xef\xbb\xbfbye\n"


async def test_edit_exact_match_preserves_untouched_trailing_whitespace(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"keep  \nfoo\n")
    tool = create_edit_tool(str(tmp_path))
    await tool.execute("t1", _params("f.txt", ("foo", "bar")))
    assert (tmp_path / "f.txt").read_bytes() == b"keep  \nbar\n"


# --- fuzzy matching ---


async def test_edit_fuzzy_matches_smart_quotes(tmp_path):
    (tmp_path / "f.txt").write_bytes("say \u201chello\u201d\n".encode())
    tool = create_edit_tool(str(tmp_path))
    await tool.execute("t1", _params("f.txt", ('say "hello"', 'say "bye"')))
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == 'say "bye"\n'


async def test_edit_fuzzy_matches_trailing_whitespace(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"foo  \nbar\nqux\n")
    tool = create_edit_tool(str(tmp_path))
    await tool.execute("t1", _params("f.txt", ("foo\nbar", "foo\nbaz")))
    assert (tmp_path / "f.txt").read_bytes() == b"foo\nbaz\nqux\n"


async def test_edit_fuzzy_preserves_unchanged_lines_original_bytes(tmp_path):
    (tmp_path / "f.txt").write_bytes("keep  \nsay \u201chi\u201d\n".encode())
    tool = create_edit_tool(str(tmp_path))
    await tool.execute("t1", _params("f.txt", ('say "hi"', 'say "yo"')))
    # The untouched first line keeps its trailing spaces; only the edited line
    # is rewritten from the fuzzy-normalized base.
    assert (tmp_path / "f.txt").read_bytes() == b'keep  \nsay "yo"\n'


# --- prepare_arguments tolerance ---


def test_prepare_arguments_merges_legacy_top_level_old_new_text():
    prepared = prepare_edit_arguments({"path": "f.txt", "oldText": "a", "newText": "b"})
    assert prepared == {"path": "f.txt", "edits": [{"oldText": "a", "newText": "b"}]}


def test_prepare_arguments_parses_edits_json_string():
    raw = {"path": "f.txt", "edits": '[{"oldText": "a", "newText": "b"}]'}
    prepared = prepare_edit_arguments(raw)
    assert prepared == {"path": "f.txt", "edits": [{"oldText": "a", "newText": "b"}]}
    assert raw["edits"] != prepared["edits"]  # input not mutated


def test_prepare_arguments_leaves_valid_input_unchanged():
    args = {"path": "f.txt", "edits": [{"oldText": "a", "newText": "b"}]}
    assert prepare_edit_arguments(args) == args
    assert prepare_edit_arguments("not a dict") == "not a dict"


async def test_edit_legacy_arguments_end_to_end(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello world\n")
    tool = create_edit_tool(str(tmp_path))
    # Mirror the agent_loop path: prepare_arguments -> validation -> execute.
    prepared = tool.prepare_arguments({"path": "f.txt", "oldText": "world", "newText": "python"})
    params = validate_tool_arguments(tool, {"id": "1", "name": "edit", "arguments": prepared})
    result = await tool.execute("t1", params)
    assert _text(result) == "Successfully replaced 1 block(s) in f.txt."
    assert (tmp_path / "f.txt").read_bytes() == b"hello python\n"


# --- abort ---


async def test_edit_aborted_signal_raises_before_writing(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello\n")
    tool = create_edit_tool(str(tmp_path))
    with pytest.raises(RuntimeError, match="Operation aborted"):
        await tool.execute("t1", _params("f.txt", ("hello", "bye")), signal=_Signal(aborted=True))
    assert (tmp_path / "f.txt").read_bytes() == b"hello\n"
