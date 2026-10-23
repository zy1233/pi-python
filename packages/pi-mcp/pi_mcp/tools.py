"""Tool names, and the shape of a tool result (design §5.3).

The call itself, with its time limit and its reconnect, is ``ServerConnection.call``. This module
has what is left: the name the model sees, and what comes back.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pi_agent_core.coding_tools.truncate import format_size
from pi_agent_core.types import AgentToolResult
from pi_mcp.settings import MAX_RESULT_BYTES

NAME_PREFIX = "mcp__"
NAME_LIMIT = 64
"""Several model providers take at most 64 characters in a function name (the pattern of
``pi_agent_core`` allows 128, and the provider then refuses the whole request)."""

_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")
_NO_OUTPUT = "(The tool returned no content.)"


def _fit(name: str) -> str:
    """*name* cut to ``NAME_LIMIT``, with a hash of the whole name at the end when it was cut."""
    if len(name) <= NAME_LIMIT:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: NAME_LIMIT - 9]}_{digest}"


def public_name(server: str, tool: str, taken: set[str]) -> str:
    """The name of an MCP tool for the model: ``mcp__<server>__<tool>``, cleaned and unique.

    Characters outside ``[a-zA-Z0-9_-]`` become ``_``. A name that is too long is cut, with a
    hash at the end. Two tools that come out the same get ``_2``, ``_3``. The name is added to
    *taken*. The prefix keeps an MCP tool from replacing a built-in one: the harness lets an
    extension tool *override* a tool of the same name, so a server with a tool called ``bash``
    would otherwise take the place of the real ``bash``.
    """
    base = f"{NAME_PREFIX}{_UNSAFE.sub('_', server)}__{_UNSAFE.sub('_', tool)}"
    candidate = _fit(base)
    number = 2
    while candidate in taken:
        candidate = _fit(f"{base}_{number}")
        number += 1
    taken.add(candidate)
    return candidate


def _cut(text: str, max_bytes: int) -> str:
    """The first *max_bytes* bytes of *text* (as UTF-8), ending on a character."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def shape_result(
    result: AgentToolResult, *, server: str, tool: str, limit: int = MAX_RESULT_BYTES
) -> AgentToolResult:
    """Make a result ready for the model.

    - Only ``structuredContent``, no text: the JSON of it is the text. The model would see
      nothing otherwise. When there is text, ``structuredContent`` is dropped, for it says the
      same and would make the session file twice as large.
    - More than *limit* bytes of text: the first part is kept, and a line says what was cut.
      Images stay as they are.
    - ``details`` tell where the result came from: ``{"mcp": {"server": ..., "tool": ...}}``.
    """
    blocks: list[Any] = list(result.content)
    details = result.details if isinstance(result.details, dict) else {}
    artifact = details.get("artifact")
    structured = artifact.get("structured_content") if isinstance(artifact, dict) else None

    def has_text() -> bool:
        return any(b.get("type") == "text" and str(b.get("text", "")).strip() for b in blocks)

    if not has_text() and structured is not None:
        blocks = [b for b in blocks if b.get("type") != "text"]
        blocks.append(
            {"type": "text", "text": json.dumps(structured, ensure_ascii=False, default=str)}
        )
    if not has_text() and not any(b.get("type") != "text" for b in blocks):
        blocks = [{"type": "text", "text": _NO_OUTPUT}]

    blocks = _limit_text(blocks, limit)
    return AgentToolResult(
        content=blocks,
        details={"mcp": {"server": server, "tool": tool}},
        terminate=result.terminate,
    )


def _limit_text(blocks: list[Any], limit: int) -> list[Any]:
    total = sum(
        len(str(b.get("text", "")).encode("utf-8")) for b in blocks if b.get("type") == "text"
    )
    if total <= limit:
        return blocks
    kept: list[Any] = []
    room = limit
    for block in blocks:
        if block.get("type") != "text":
            kept.append(block)
            continue
        if room <= 0:
            continue  # the budget is spent: later text is dropped
        text = str(block.get("text", ""))
        part = _cut(text, room)
        room -= len(part.encode("utf-8"))
        kept.append({"type": "text", "text": part})
    note = {
        "type": "text",
        "text": f"[Output cut: the first {format_size(limit)} of {format_size(total)} are shown.]",
    }
    last_text = max((i for i, b in enumerate(kept) if b.get("type") == "text"), default=-1)
    kept.insert(last_text + 1, note)
    return kept
