"""Cross-provider message transforms for model switching and replay."""

from __future__ import annotations

import secrets
import string

from pi_agent_core.messages import (
    AssistantMessage,
    ContentBlock,
    ImageContent,
    Message,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from pi_agent_core.types import Model

_ALPHANUM = string.ascii_lowercase + string.digits


def _random_suffix(length: int = 24) -> str:
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def _is_openai_tool_id(tool_id: str) -> bool:
    return tool_id.startswith("call_")


def _is_anthropic_tool_id(tool_id: str) -> bool:
    return tool_id.startswith("toolu_")


def _target_provider_prefix(provider: str) -> str:
    if provider.lower() == "anthropic":
        return "toolu_"
    return "call_"


def _rewrite_tool_id(tool_id: str, target_prefix: str) -> str:
    if tool_id.startswith(target_prefix):
        return tool_id
    if target_prefix == "toolu_":
        suffix = tool_id.removeprefix("call_") if _is_openai_tool_id(tool_id) else tool_id
    else:
        suffix = tool_id.removeprefix("toolu_") if _is_anthropic_tool_id(tool_id) else tool_id
    if not suffix or suffix == tool_id:
        suffix = _random_suffix()
    return f"{target_prefix}{suffix}"


def normalize_tool_call_ids(
    messages: list[Message],
    target_model: Model,
) -> list[Message]:
    """Rewrite tool call IDs to match the target provider format."""
    target_prefix = _target_provider_prefix(target_model.provider)
    id_map: dict[str, str] = {}
    out: list[Message] = []

    for msg in messages:
        if isinstance(msg, AssistantMessage):
            new_content: list[ContentBlock] = []
            for block in msg.content:
                if block.get("type") != "toolCall":
                    new_content.append(block)
                    continue
                old_id = block["id"]
                if old_id not in id_map:
                    id_map[old_id] = _rewrite_tool_id(old_id, target_prefix)
                new_content.append({**block, "id": id_map[old_id]})
            out.append(msg.model_copy(update={"content": new_content}))
        elif isinstance(msg, ToolResultMessage):
            new_id = id_map.get(msg.toolCallId, msg.toolCallId)
            if new_id == msg.toolCallId and not (
                (target_prefix == "call_" and _is_openai_tool_id(msg.toolCallId))
                or (target_prefix == "toolu_" and _is_anthropic_tool_id(msg.toolCallId))
            ):
                new_id = _rewrite_tool_id(msg.toolCallId, target_prefix)
                id_map[msg.toolCallId] = new_id
            out.append(msg.model_copy(update={"toolCallId": new_id}))
        else:
            out.append(msg)

    return out


def downgrade_thinking(messages: list[Message], target_model: Model) -> list[Message]:
    """Remove ThinkingContent blocks when the target model does not support reasoning."""
    if target_model.reasoning:
        return messages

    out: list[Message] = []
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            out.append(msg)
            continue
        filtered = [b for b in msg.content if b.get("type") != "thinking"]
        out.append(msg.model_copy(update={"content": filtered}))
    return out


def strip_unsupported_images(messages: list[Message], target_model: Model) -> list[Message]:
    """Remove image blocks when the target model does not support images."""
    if target_model.supports_images:
        return messages

    placeholder: TextContent = {"type": "text", "text": "[image content removed]"}
    out: list[Message] = []

    for msg in messages:
        if not isinstance(msg, UserMessage):
            out.append(msg)
            continue
        if isinstance(msg.content, str):
            out.append(msg)
            continue
        blocks: list[TextContent | ImageContent] = []
        for block in msg.content:
            if block.get("type") == "image":
                continue
            blocks.append(block)
        if not blocks:
            out.append(msg.model_copy(update={"content": [placeholder]}))
        else:
            out.append(msg.model_copy(update={"content": blocks}))
    return out


def transform_messages(
    messages: list[Message],
    target_model: Model,
    source_model: Model | None = None,
) -> list[Message]:
    """Apply cross-provider message transforms for the target model."""
    del source_model
    result = messages
    result = normalize_tool_call_ids(result, target_model)
    result = downgrade_thinking(result, target_model)
    result = strip_unsupported_images(result, target_model)
    return result
