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
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)

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

SessionUpdate = (
    AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | UserMessageChunk
)


def tool_kind(name: str) -> str:
    return _KIND.get(name, "other")


def project_event(event: AgentEvent) -> Iterator[SessionUpdate]:
    etype = getattr(event, "type", None)
    if etype == "message_end":
        message = getattr(event, "message", None)
        stop = getattr(message, "stopReason", None)
        err = getattr(message, "errorMessage", None)
        if stop in ("error", "aborted") and err:
            yield update_agent_message_text(f"Error: {err}")
        return

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


def project_message_replay(message: Any) -> Iterator[SessionUpdate]:
    """Project a stored historical message onto ACP session_update events with replay meta."""
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")

    replay_meta: dict[str, Any] = {"isReplay": True}

    if role == "user":
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif getattr(item, "type", None) == "text":
                    parts.append(str(getattr(item, "text", "")))
            text = "".join(parts)
        if text:
            chunk = UserMessageChunk(
                content=text_block(text),
                session_update="user_message_chunk",
                field_meta=replay_meta,
            )
            yield chunk
        return

    if role == "assistant":
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if not isinstance(content, list):
            return

        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype == "thinking":
                t = (
                    block.get("thinking")
                    if isinstance(block, dict)
                    else getattr(block, "thinking", None)
                )
                if t:
                    chunk = update_agent_thought_text(str(t))
                    chunk.field_meta = replay_meta
                    yield chunk
            elif btype == "text":
                txt = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
                if txt:
                    chunk = update_agent_message_text(str(txt))
                    chunk.field_meta = replay_meta
                    yield chunk
            elif btype == "toolCall":
                tc_id = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
                tc_name = (
                    block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
                )
                tc_args = (
                    block.get("arguments")
                    if isinstance(block, dict)
                    else getattr(block, "arguments", None)
                )
                if tc_id and tc_name:
                    tc = start_tool_call(
                        str(tc_id),
                        str(tc_name),
                        kind=tool_kind(str(tc_name)),  # type: ignore[arg-type]
                        status="completed",
                        raw_input=tc_args,
                    )
                    tc.field_meta = replay_meta
                    yield tc
        return

    if role == "toolResult":
        tc_id = getattr(message, "toolCallId", None)
        if tc_id is None and isinstance(message, dict):
            tc_id = message.get("toolCallId")
        tc_name = getattr(message, "toolName", None)
        if tc_name is None and isinstance(message, dict):
            tc_name = message.get("toolName")
        is_err = getattr(message, "isError", False)
        if is_err is False and isinstance(message, dict):
            is_err = message.get("isError", False)
        details = getattr(message, "details", None)
        if details is None and isinstance(message, dict):
            details = message.get("details")

        if tc_id:
            txt = _text_from_result(message)
            content_list = None
            if tc_name and tc_name in {"edit", "write"}:
                content_list = _result_content(tc_name, details, message)
            if not content_list and txt:
                content_list = [tool_content(text_block(txt))]
            status = "failed" if is_err else "completed"
            update = update_tool_call(
                str(tc_id),
                status=status,
                content=content_list,
                raw_output=txt or None,
            )
            update.field_meta = replay_meta
            yield update
        return
