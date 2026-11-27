"""Ephemeral ``/btw`` side-chat runner.

Reads a read-only snapshot of the main session's LLM context, repairs any dangling in-flight
tool calls, projects tool history into tool-free plain text (preventing cross-provider HTTP 400
errors when ``tools=[]``), and streams a side answer without writing anything to the main
session JSONL or touching the harness phase lock.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pi_agent_core.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from pi_agent_core.transform import transform_messages
from pi_agent_core.types import (
    AgentContext,
    AgentMessage,
    Model,
    StreamFn,
    StreamOptions,
    ThinkingLevel,
)
from pi_agent_harness import BtwContextSnapshot
from pi_agent_harness.messages import harness_convert_to_llm

BTW_MAX_HISTORY_TURNS = 10
BTW_MAX_HISTORY_BYTES = 32 * 1024
BTW_MAX_CONCURRENT_PER_SESSION = 2
_MAX_TOOL_ARGS_CHARS = 400
_MAX_TOOL_RESULT_CHARS = 4000

_IN_FLIGHT_TOOL_PLACEHOLDER = (
    "[Tool is still running in the main session; output not yet available.]"
)

_BTW_INSTRUCTION = (
    "This is an ephemeral side question (/btw) outside the main conversation. "
    "You have NO tools available here and cannot inspect files or run commands. "
    "Answer concisely using only the existing conversation context above. "
    "Do not attempt to invoke tools or claim you executed any action."
)


class BtwAbortSignal:
    """Abort signal compatible with ``StreamFn`` / ``StreamOptions.signal``."""

    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    async def wait_aborted(self) -> None:
        await self._event.wait()

    def _abort(self) -> None:
        self.aborted = True
        self._event.set()


class BtwAbortController:
    def __init__(self) -> None:
        self.signal = BtwAbortSignal()

    def abort(self) -> None:
        self.signal._abort()


@dataclass(frozen=True)
class BtwHistoryTurn:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class BtwResult:
    stop_reason: Literal["end_turn", "cancelled", "error"]
    text: str = ""
    error: str | None = None


BtwUpdateCallback = Callable[[Literal["thought_delta", "text_delta"], str], Awaitable[None]]


def parse_btw_history(raw: Any) -> list[BtwHistoryTurn]:
    """Validate optional client-supplied ``/btw`` overlay history (bounded in turns and bytes)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("history must be a list")
    if len(raw) > BTW_MAX_HISTORY_TURNS * 2:
        raise ValueError(f"history exceeds maximum of {BTW_MAX_HISTORY_TURNS} turns")
    turns: list[BtwHistoryTurn] = []
    total_bytes = 0
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"history[{idx}] must be an object")
        role = entry.get("role")
        text = entry.get("text")
        if role not in ("user", "assistant"):
            raise ValueError(f"history[{idx}].role must be 'user' or 'assistant'")
        if not isinstance(text, str):
            raise ValueError(f"history[{idx}].text must be a string")
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > BTW_MAX_HISTORY_BYTES:
            raise ValueError(f"history exceeds {BTW_MAX_HISTORY_BYTES} bytes")
        if text.strip():
            turns.append(BtwHistoryTurn(role=role, text=text))
    return turns


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        btype = block.get("type")
        return str(btype) if btype is not None else None
    btype = getattr(block, "type", None)
    return str(btype) if btype is not None else None


def _block_field(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _message_role(message: Any) -> str | None:
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
    return str(role) if role is not None else None


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def repair_dangling_tool_calls(messages: Sequence[AgentMessage]) -> list[AgentMessage]:
    """Ensure every assistant ``toolCall`` block has a matching ``toolResult`` message.

    When ``/btw`` runs while the main session is executing tools, the latest persisted assistant
    message contains ``toolCall`` blocks with no ``toolResult`` yet. OpenAI and Anthropic reject
    unpaired tool calls with HTTP 400; inserting synthetic in-memory results keeps the grammar
    valid and tells the model which tool is still running.
    """
    out: list[AgentMessage] = []
    n = len(messages)
    i = 0
    while i < n:
        msg = messages[i]
        out.append(msg)
        if _message_role(msg) != "assistant":
            i += 1
            continue
        content = _message_content(msg)
        tool_calls: list[tuple[str, str]] = []
        if isinstance(content, list):
            for block in content:
                if _block_type(block) == "toolCall":
                    tc_id = str(_block_field(block, "id") or "")
                    tc_name = str(_block_field(block, "name") or "tool")
                    if tc_id:
                        tool_calls.append((tc_id, tc_name))
        if not tool_calls:
            i += 1
            continue

        # Collect the contiguous toolResult run immediately following this assistant message.
        seen_ids: set[str] = set()
        j = i + 1
        while j < n and _message_role(messages[j]) == "toolResult":
            res = messages[j]
            out.append(res)
            tc_id = (
                res.get("toolCallId") if isinstance(res, dict) else getattr(res, "toolCallId", None)
            )
            if isinstance(tc_id, str) and tc_id:
                seen_ids.add(tc_id)
            j += 1

        now_ms = int(time.time() * 1000)
        for tc_id, tc_name in tool_calls:
            if tc_id not in seen_ids:
                out.append(
                    ToolResultMessage(
                        toolCallId=tc_id,
                        toolName=tc_name,
                        content=[{"type": "text", "text": _IN_FLIGHT_TOOL_PLACEHOLDER}],
                        isError=False,
                        timestamp=now_ms,
                    )
                )
        i = j
    return out


def _compact_args(args: Any) -> str:
    try:
        rendered = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        rendered = str(args)
    if len(rendered) > _MAX_TOOL_ARGS_CHARS:
        return rendered[:_MAX_TOOL_ARGS_CHARS] + "..."
    return rendered


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _extract_text_from_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if _block_type(block) == "text":
            txt = _block_field(block, "text", "")
            if txt:
                parts.append(str(txt))
    return "".join(parts)


def _append_user_text(out: list[AgentMessage], text: str, images: list[ImageContent] | None = None):
    blocks: list[TextContent | ImageContent] = []
    if text:
        blocks.append({"type": "text", "text": text})
    if images:
        blocks.extend(images)
    if not blocks:
        return
    if out and _message_role(out[-1]) == "user":
        prev = out[-1]
        prev_content = _message_content(prev)
        merged: list[TextContent | ImageContent] = []
        if isinstance(prev_content, str):
            if prev_content:
                merged.append({"type": "text", "text": prev_content})
        elif isinstance(prev_content, list):
            merged.extend(prev_content)
        if text and merged and _block_type(merged[-1]) == "text":
            last_text = str(_block_field(merged[-1], "text", ""))
            merged[-1] = {"type": "text", "text": f"{last_text}\n\n{text}"}
            if images:
                merged.extend(images)
        else:
            merged.extend(blocks)
        out[-1] = UserMessage(content=merged)
        return
    out.append(UserMessage(content=blocks))


def flatten_tool_history_for_btw(
    messages: Sequence[AgentMessage],
    *,
    model: Model,
) -> list[AgentMessage]:
    """Convert main-session history into a tool-free user/assistant transcript for ``/btw``.

    Anthropic rejects ``tool_use``/``tool_result`` blocks when ``tools=[]`` and several
    OpenAI-format gateways behave inconsistently with empty tool lists. Projecting tool calls and
    results into compact readable text guarantees HTTP 200 across every provider and prevents any
    tool execution.
    """
    repaired = repair_dangling_tool_calls(messages)
    llm_messages = harness_convert_to_llm(repaired)
    out: list[AgentMessage] = []

    for msg in llm_messages:
        role = _message_role(msg)
        content = _message_content(msg)

        if role == "user":
            if isinstance(content, str):
                _append_user_text(out, content)
            elif isinstance(content, list):
                text_parts: list[str] = []
                images: list[ImageContent] = []
                for block in content:
                    btype = _block_type(block)
                    if btype == "text":
                        txt = _block_field(block, "text", "")
                        if txt:
                            text_parts.append(str(txt))
                    elif btype == "image":
                        data = str(_block_field(block, "data", "") or "")
                        mime = str(_block_field(block, "mimeType", "image/png") or "image/png")
                        if data:
                            images.append({"type": "image", "data": data, "mimeType": mime})
                _append_user_text(out, "\n".join(text_parts), images or None)
            continue

        if role == "assistant":
            text_parts = []
            if isinstance(content, str) and content:
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    btype = _block_type(block)
                    if btype == "text":
                        txt = _block_field(block, "text", "")
                        if txt:
                            text_parts.append(str(txt))
                    elif btype == "toolCall":
                        tc_name = str(_block_field(block, "name") or "tool")
                        tc_args = _block_field(block, "arguments", {})
                        text_parts.append(f"[Tool call: {tc_name}({_compact_args(tc_args)})]")
                    # Thinking blocks are omitted so stale signatures never fail cross-provider.
            combined = "\n".join(part for part in text_parts if part)
            if not combined:
                continue
            if out and _message_role(out[-1]) == "assistant":
                prev_text = _extract_text_from_blocks(_message_content(out[-1]))
                combined = f"{prev_text}\n\n{combined}" if prev_text else combined
                out[-1] = AssistantMessage(
                    content=[{"type": "text", "text": combined}],
                    api=model.api,
                    provider=model.provider,
                    model=model.model_id,
                    usage=Usage(),
                    stopReason="stop",
                )
            else:
                out.append(
                    AssistantMessage(
                        content=[{"type": "text", "text": combined}],
                        api=model.api,
                        provider=model.provider,
                        model=model.model_id,
                        usage=Usage(),
                        stopReason="stop",
                    )
                )
            continue

        if role == "toolResult":
            tc_name = (
                msg.get("toolName") if isinstance(msg, dict) else getattr(msg, "toolName", None)
            ) or "tool"
            is_err = bool(
                msg.get("isError") if isinstance(msg, dict) else getattr(msg, "isError", False)
            )
            status = "error" if is_err else "ok"
            body = _truncate_text(_extract_text_from_blocks(content), _MAX_TOOL_RESULT_CHARS)
            note = (
                f"[Tool result: {tc_name} ({status})]\n{body}"
                if body
                else (f"[Tool result: {tc_name} ({status})]")
            )
            _append_user_text(out, note)

    return out


def build_btw_context(
    snapshot: BtwContextSnapshot,
    question: str,
    images: list[ImageContent] | None = None,
    history: Sequence[BtwHistoryTurn] = (),
    *,
    model: Model | None = None,
) -> AgentContext:
    """Build a tool-free ``AgentContext`` for a ``/btw`` request from a read-only snapshot."""
    target_model = model or snapshot.model
    messages = flatten_tool_history_for_btw(snapshot.messages, model=target_model)

    for turn in history:
        if turn.role == "user":
            _append_user_text(out=messages, text=f"[Side question]\n{turn.text}")
        else:
            messages.append(
                AssistantMessage(
                    content=[{"type": "text", "text": turn.text}],
                    api=target_model.api,
                    provider=target_model.provider,
                    model=target_model.model_id,
                    usage=Usage(),
                    stopReason="stop",
                )
            )

    wrapped_question = (
        f"{_BTW_INSTRUCTION}\n\n<btw_side_question>\n{question}\n</btw_side_question>"
    )
    _append_user_text(messages, wrapped_question, images or None)
    transformed = transform_messages(messages, target_model=target_model)
    return AgentContext(
        system_prompt=snapshot.system_prompt,
        messages=transformed,
        tools=[],
    )


async def run_btw(
    *,
    snapshot: BtwContextSnapshot,
    question: str,
    stream_fn: StreamFn,
    model: Model,
    thinking_level: ThinkingLevel,
    get_api_key: Callable[[str], Awaitable[str | None] | str | None] | None = None,
    images: list[ImageContent] | None = None,
    history: Sequence[BtwHistoryTurn] = (),
    on_update: BtwUpdateCallback | None = None,
    abort_controller: BtwAbortController | None = None,
    timeout_seconds: float = 120.0,
) -> BtwResult:
    """Execute one ephemeral ``/btw`` side turn and stream text/thinking deltas to *on_update*."""
    controller = abort_controller or BtwAbortController()
    context = build_btw_context(
        snapshot,
        question,
        images=images,
        history=history,
        model=model,
    )

    api_key: str | None = None
    if get_api_key is not None:
        raw_key = get_api_key(model.provider)
        if hasattr(raw_key, "__await__"):
            api_key = await raw_key
        else:
            api_key = raw_key

    options = StreamOptions(
        api_key=api_key,
        reasoning=None if thinking_level == "off" else thinking_level,
        signal=controller.signal,
    )

    async def _consume() -> BtwResult:
        stream = stream_fn(model, context, options)
        if hasattr(stream, "__await__"):
            stream = await stream
        collected: list[str] = []
        async for event in stream:
            if controller.signal.aborted:
                return BtwResult(stop_reason="cancelled", text="".join(collected))
            etype = getattr(event, "type", None)
            if etype == "text_delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    collected.append(delta)
                    if on_update is not None:
                        await on_update("text_delta", delta)
            elif etype == "thinking_delta":
                delta = getattr(event, "delta", "") or ""
                if delta and on_update is not None:
                    await on_update("thought_delta", delta)
            elif etype == "error":
                err_msg = getattr(event, "error", None)
                reason = getattr(err_msg, "stopReason", None)
                if reason == "aborted" or controller.signal.aborted:
                    return BtwResult(stop_reason="cancelled", text="".join(collected))
                detail = getattr(err_msg, "errorMessage", None) or "Side chat failed"
                return BtwResult(stop_reason="error", text="".join(collected), error=str(detail))
            # Tool call events (if a model ever emits any despite tools=[]) are ignored (B3).
        if controller.signal.aborted:
            return BtwResult(stop_reason="cancelled", text="".join(collected))
        return BtwResult(stop_reason="end_turn", text="".join(collected))

    try:
        return await asyncio.wait_for(_consume(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        return BtwResult(stop_reason="cancelled")
    except TimeoutError:
        controller.abort()
        return BtwResult(
            stop_reason="error",
            error=f"/btw timed out after {timeout_seconds:g}s",
        )
    except Exception as exc:
        if controller.signal.aborted:
            return BtwResult(stop_reason="cancelled")
        return BtwResult(stop_reason="error", error=str(exc))
