"""Project AgentEvent onto standard ACP session/update payloads."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from acp.helpers import (
    start_tool_call,
    text_block,
    tool_content,
    tool_diff_content,
    update_agent_message_text,
    update_agent_thought_text,
    update_tool_call,
)
from acp.schema import AgentMessageChunk, AgentThoughtChunk, ToolCallProgress, ToolCallStart

from pi_agent_core.types import AgentEvent

_KIND: dict[str, str] = {
    "read": "read",
    "edit": "edit",
    "write": "edit",
    "bash": "execute",
    "grep": "search",
    "find": "search",
    "ls": "search",
}

SessionUpdate = AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress


def tool_kind(name: str) -> str:
    return _KIND.get(name, "other")


def project_event(event: AgentEvent) -> Iterator[SessionUpdate]:
    etype = getattr(event, "type", None)
    if etype == "message_update":
        ame = getattr(event, "assistant_message_event", None)
        ame_type = getattr(ame, "type", None)
        if ame_type == "text_delta":
            delta = getattr(ame, "delta", "") or ""
            if delta:
                yield update_agent_message_text(delta)
        elif ame_type == "thinking_delta":
            delta = getattr(ame, "delta", "") or ""
            if delta:
                yield update_agent_thought_text(delta)
        return

    if etype == "tool_execution_start":
        name = event.tool_name
        yield start_tool_call(
            event.tool_call_id,
            name,
            kind=tool_kind(name),  # type: ignore[arg-type]
            status="pending",
            raw_input=event.args,
        )
        return

    if etype == "tool_execution_update":
        content = _partial_content(event.partial_result)
        yield update_tool_call(
            event.tool_call_id,
            status="in_progress",
            content=content,
        )
        return

    if etype == "tool_execution_end":
        status = "failed" if event.is_error else "completed"
        args = getattr(event, "args", None)
        yield update_tool_call(
            event.tool_call_id,
            status=status,
            content=_result_content(event.tool_name, args, event.result),
            raw_output=_raw_output(event.result),
        )


def _text_from_result(result: Any) -> str:
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif getattr(block, "type", None) == "text":
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(result)


def _partial_content(partial: Any) -> list[Any] | None:
    text = _text_from_result(partial)
    if not text:
        return None
    return [tool_content(text_block(text))]


def _result_content(tool_name: str, args: Any, result: Any) -> list[Any] | None:
    details = getattr(result, "details", None) if result is not None else None
    path = None
    if isinstance(args, dict):
        path = args.get("path")
    if path is None and isinstance(details, dict):
        path = details.get("path")
    if tool_name in {"edit", "write"} and path:
        patch = None
        if isinstance(details, dict):
            patch = details.get("diff") or details.get("patch")
        if patch:
            return [tool_diff_content(str(path), str(patch))]
        text = _text_from_result(result)
        if text:
            return [tool_diff_content(str(path), text)]
    text = _text_from_result(result)
    if not text:
        return None
    return [tool_content(text_block(text))]


def _raw_output(result: Any) -> Any:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(exclude_none=True)
    return result
