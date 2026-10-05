"""ACP ``session/list`` display data: first-user-message titles and last-activity times."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pi_agent_cli.session_list import (
    NO_MESSAGES_TITLE,
    PREVIEW_MAX_LINES,
    TITLE_MAX_CHARS,
    read_session_preview,
    read_session_previews,
)

CREATED_AT = "2026-01-01T00:00:00.000Z"
HEADER = {"type": "session", "version": 3, "id": "s1", "timestamp": CREATED_AT, "cwd": "/w"}


def _message(role: str, content: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "id": f"m-{role}",
        "parentId": None,
        "timestamp": CREATED_AT,
        "message": {"role": role, "content": content},
    }


def _write(path: Path, *entries: dict[str, Any] | str) -> Path:
    lines = [e if isinstance(e, str) else json.dumps(e) for e in (HEADER, *entries)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _text(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def test_title_is_the_first_user_message_with_whitespace_collapsed(tmp_path):
    path = _write(
        tmp_path / "s.jsonl",
        _message("user", _text("fix the\n  failing   test")),
        _message("user", _text("second prompt")),
    )
    assert read_session_preview(path, CREATED_AT).title == "fix the failing test"


def test_string_content_and_non_text_blocks(tmp_path):
    plain = _write(tmp_path / "plain.jsonl", _message("user", "plain string"))
    assert read_session_preview(plain, CREATED_AT).title == "plain string"

    mixed = _write(
        tmp_path / "mixed.jsonl",
        _message("user", [{"type": "image", "data": "x", "mimeType": "image/png"}]),
        _message("user", [*_text("with"), {"type": "image", "data": "x"}, *_text("image")]),
    )
    # The image-only prompt has no text: the next user message names the session.
    assert read_session_preview(mixed, CREATED_AT).title == "with image"


def test_assistant_and_other_entries_do_not_name_the_session(tmp_path):
    path = _write(
        tmp_path / "s.jsonl",
        {"type": "model_change", "id": "x", "parentId": None, "timestamp": CREATED_AT},
        _message("assistant", _text("I speak first")),
        _message("user", _text("the real prompt")),
    )
    assert read_session_preview(path, CREATED_AT).title == "the real prompt"


def test_long_titles_are_capped_with_an_ellipsis(tmp_path):
    path = _write(tmp_path / "s.jsonl", _message("user", _text("word " * 100)))
    title = read_session_preview(path, CREATED_AT).title
    assert len(title) == TITLE_MAX_CHARS
    assert title.endswith("\u2026")
    assert "  " not in title


def test_session_without_a_user_message_has_the_placeholder_title(tmp_path):
    header_only = _write(tmp_path / "empty.jsonl")
    assert read_session_preview(header_only, CREATED_AT).title == NO_MESSAGES_TITLE

    assistant_only = _write(tmp_path / "a.jsonl", _message("assistant", _text("hi")))
    assert read_session_preview(assistant_only, CREATED_AT).title == NO_MESSAGES_TITLE


def test_only_a_bounded_prefix_of_the_file_is_read(tmp_path):
    filler = [{"type": "thinking_level_change", "id": str(i)} for i in range(PREVIEW_MAX_LINES)]
    path = _write(tmp_path / "s.jsonl", *filler, _message("user", _text("too far down")))
    assert read_session_preview(path, CREATED_AT).title == NO_MESSAGES_TITLE


def test_corrupt_lines_are_skipped(tmp_path):
    path = _write(
        tmp_path / "s.jsonl",
        "{not json",
        "[1, 2, 3]",
        _message("user", _text("survives")),
    )
    assert read_session_preview(path, CREATED_AT).title == "survives"


def test_updated_at_is_the_file_modification_time(tmp_path):
    path = _write(tmp_path / "s.jsonl", _message("user", _text("hi")))
    stamp = datetime(2024, 5, 6, 7, 8, 9, 250_000, tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))
    assert read_session_preview(path, CREATED_AT).updated_at == "2024-05-06T07:08:09.250Z"


def test_missing_file_falls_back_to_the_created_at_stamp(tmp_path):
    preview = read_session_preview(tmp_path / "gone.jsonl", CREATED_AT)
    assert preview.title == NO_MESSAGES_TITLE
    assert preview.updated_at == CREATED_AT


def test_read_session_previews_keeps_the_input_order(tmp_path):
    first = _write(tmp_path / "a.jsonl", _message("user", _text("alpha")))
    second = _write(tmp_path / "b.jsonl", _message("user", _text("beta")))
    previews = read_session_previews([(second, CREATED_AT), (first, CREATED_AT)])
    assert [p.title for p in previews] == ["beta", "alpha"]
