"""Project AgentEvent onto standard ACP session/update payloads."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from acp.helpers import (
    start_tool_call,
    text_block,
    tool_content,
    tool_diff_content,
    tool_terminal_ref,
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

_terminal_stream_offsets: dict[str, int] = {}


def tool_title(name: str, args: Any = None) -> str:
    """Derive a human-readable title for a tool call (displayed in ACP clients like Zed)."""
    if isinstance(args, dict):
        if name == "bash":
            cmd = args.get("command")
            if cmd:
                return str(cmd)
        elif name in {"write", "edit", "read"}:
            path = args.get("path")
            if path:
                return f"{name} {path}"
        elif name in {"grep", "find"}:
            pattern = args.get("pattern")
            path = args.get("path")
            if pattern and path:
                return f"{name} {pattern} {path}"
            if pattern:
                return f"{name} {pattern}"
        elif name == "ls":
            path = args.get("path", ".")
            return f"ls {path}"
    return name


def _extract_exit_code(result: Any, is_error: bool) -> int:
    if not is_error:
        return 0
    details = getattr(result, "details", None)
    if isinstance(details, dict) and "exit_code" in details:
        try:
            return int(details["exit_code"])
        except (ValueError, TypeError):
            pass
    text = _text_from_result(result)
    m = re.search(r"Command exited with code (\d+)", text)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass
    return 1


SessionUpdate = (
    AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | UserMessageChunk
)


def tool_kind(name: str) -> str:
    return _KIND.get(name, "other")


def project_event(event: AgentEvent, cwd: str | None = None) -> Iterator[SessionUpdate]:
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
        title = tool_title(name, event.args)
        field_meta: dict[str, Any] | None = None
        content: list[Any] | None = None
        if name == "bash":
            _terminal_stream_offsets[event.tool_call_id] = 0
            term_info: dict[str, Any] = {"terminal_id": event.tool_call_id}
            if cwd:
                term_info["cwd"] = str(cwd)
            field_meta = {"terminal_info": term_info}
            content = [tool_terminal_ref(event.tool_call_id)]

        start = start_tool_call(
            event.tool_call_id,
            title,
            kind=tool_kind(name),  # type: ignore[arg-type]
            status="pending",
            raw_input=event.args,
            content=content,
        )
        if field_meta:
            start.field_meta = field_meta
        yield start
        return

    if etype == "tool_execution_update":
        name = getattr(event, "tool_name", "")
        field_meta: dict[str, Any] | None = None
        content: list[Any] | None = None
        if name == "bash":
            text = _text_from_result(event.partial_result)
            offset = _terminal_stream_offsets.get(event.tool_call_id, 0)
            delta = text[offset:]
            if delta:
                _terminal_stream_offsets[event.tool_call_id] = len(text)
                field_meta = {
                    "terminal_output": {
                        "terminal_id": event.tool_call_id,
                        "data": delta,
                    }
                }
            content = [tool_terminal_ref(event.tool_call_id)]
            if text:
                content.append(tool_content(text_block(text)))
        else:
            content = _partial_content(event.partial_result)

        prog = update_tool_call(
            event.tool_call_id,
            status="in_progress",
            content=content,
        )
        if field_meta:
            prog.field_meta = field_meta
        yield prog
        return

    if etype == "tool_execution_end":
        name = event.tool_name
        status = "failed" if event.is_error else "completed"
        args = getattr(event, "args", None)
        field_meta: dict[str, Any] | None = None
        if name == "bash":
            text = _text_from_result(event.result)
            offset = _terminal_stream_offsets.pop(event.tool_call_id, 0)
            delta = text[offset:]
            exit_code = _extract_exit_code(event.result, event.is_error)
            term_meta: dict[str, Any] = {
                "terminal_exit": {
                    "terminal_id": event.tool_call_id,
                    "exit_code": exit_code,
                }
            }
            if delta:
                term_meta["terminal_output"] = {
                    "terminal_id": event.tool_call_id,
                    "data": delta,
                }
            field_meta = term_meta
            content_list: list[Any] = [tool_terminal_ref(event.tool_call_id)]
            if text:
                content_list.append(tool_content(text_block(text)))
        else:
            content_list = _result_content(name, args, event.result)

        prog = update_tool_call(
            event.tool_call_id,
            status=status,
            content=content_list,
            raw_output=_raw_output(event.result),
        )
        if field_meta:
            prog.field_meta = field_meta
        yield prog
        return


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
                    tc_title = tool_title(str(tc_name), tc_args)
                    content = None
                    replay_tool_meta = dict(replay_meta)
                    if str(tc_name) == "bash":
                        replay_tool_meta["terminal_info"] = {"terminal_id": str(tc_id)}
                        content = [tool_terminal_ref(str(tc_id))]
                    tc = start_tool_call(
                        str(tc_id),
                        tc_title,
                        kind=tool_kind(str(tc_name)),  # type: ignore[arg-type]
                        status="completed",
                        raw_input=tc_args,
                        content=content,
                    )
                    tc.field_meta = replay_tool_meta
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
            replay_result_meta = dict(replay_meta)
            if str(tc_name) == "bash":
                exit_code = _extract_exit_code(message, is_err)
                if txt:
                    replay_result_meta["terminal_output"] = {
                        "terminal_id": str(tc_id),
                        "data": txt,
                    }
                replay_result_meta["terminal_exit"] = {
                    "terminal_id": str(tc_id),
                    "exit_code": exit_code,
                }
                content_list = [tool_terminal_ref(str(tc_id))]
                if txt:
                    content_list.append(tool_content(text_block(txt)))
            elif tc_name and tc_name in {"edit", "write"}:
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
            update.field_meta = replay_result_meta
            yield update
        return
