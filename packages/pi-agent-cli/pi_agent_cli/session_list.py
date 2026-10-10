"""Display data for ACP ``session/list``: a human title and the last-activity time, and the
cursor that pages the list.

The title follows pi's session selector: the first user message of the session, with
whitespace collapsed and a length cap. Only a bounded prefix of each session file is read,
so listing stays proportional to the number of sessions, not to their size. The last
activity is the session file's modification time (every entry is an append).

A page holds ``SESSION_LIST_PAGE_SIZE`` sessions, newest first (by creation, as the harness
lists them). ``nextCursor`` is opaque to the client; here it carries the file name the next
page continues below, so it needs no state in the agent: it survives a restart, and a session
created or deleted in between moves nothing that was not already moved.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Sessions per ``session/list`` response. A page costs the files it looks at (the harness
# reads a header only when the page gets that far), so this bounds the work of one request
# and the size of the answer; the TUI keeps asking until ``nextCursor`` is gone.
SESSION_LIST_PAGE_SIZE = 50

NO_MESSAGES_TITLE = "(no messages)"
TITLE_MAX_CHARS = 100
# The header line plus the entries written before the first prompt (model / thinking-level
# changes) always fit in this window; a session whose first user message is further down has
# no user message worth showing.
PREVIEW_MAX_LINES = 200


def encode_cursor(after: str) -> str:
    """The opaque ``nextCursor`` of a page whose listing carries on below file name ``after``."""
    raw = json.dumps({"after": after}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> str:
    """The file name ``encode_cursor`` was given; ``ValueError`` for any other string."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        body = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    except ValueError as exc:  # also binascii.Error, JSONDecodeError and UnicodeDecodeError
        raise ValueError("not a cursor") from exc
    after = body.get("after") if isinstance(body, dict) else None
    # A file name from the sessions directory, nothing that could name another place.
    if (
        not isinstance(after, str)
        or not after.endswith(".jsonl")
        or len(after) > 255
        or any(sep in after for sep in ("/", "\\", "\0"))
    ):
        raise ValueError("not a cursor")
    return after


@dataclass(frozen=True)
class SessionPreview:
    title: str
    updated_at: str


def read_session_previews(sessions: Sequence[tuple[str | Path, str]]) -> list[SessionPreview]:
    """Preview every ``(path, created_at)`` pair; blocking file I/O, run it off the loop."""
    return [read_session_preview(path, created_at) for path, created_at in sessions]


def read_session_preview(path: str | Path, created_at: str) -> SessionPreview:
    return SessionPreview(
        title=_first_user_title(path) or NO_MESSAGES_TITLE,
        updated_at=_modified_at(path) or created_at,
    )


def _first_user_title(path: str | Path) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= PREVIEW_MAX_LINES:
                    return None
                if index == 0:  # session header
                    continue
                title = _title_of_entry(line)
                if title:
                    return title
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _title_of_entry(line: str) -> str | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "message":
        return None
    message = entry.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    return _normalize_title(_user_text(message)) or None


def _user_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _normalize_title(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: TITLE_MAX_CHARS - 1].rstrip() + "\u2026"


def _modified_at(path: str | Path) -> str | None:
    try:
        modified = os.stat(path).st_mtime
    except OSError:
        return None
    stamp = datetime.fromtimestamp(modified, tz=UTC).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")
