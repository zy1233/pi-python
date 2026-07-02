"""Convert pi messages to LangChain BaseMessage."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from pi_agent_core.messages import AssistantMessage, Message, ToolResultMessage, UserMessage
from pi_agent_core.types import AgentMessage, Model


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    result: list[Message] = []
    for m in messages:
        role = getattr(m, "role", None)
        if role in ("user", "assistant", "toolResult"):
            result.append(m)
    return result


def _image_block_to_lc(block: dict) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{block['mimeType']};base64,{block['data']}"},
    }


def _user_content_to_lc(content: str | list) -> str | list:
    if isinstance(content, str):
        return content
    parts: list = []
    for block in content:
        if block.get("type") == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            parts.append(_image_block_to_lc(block))
    return parts if parts else ""


def convert_to_langchain(
    messages: list[Message],
    system_prompt: str | None = None,
    model: Model | None = None,
) -> list[BaseMessage]:
    """Convert pi messages to LangChain messages.

    `model` drives provider-specific handling of tool-result images: Anthropic
    accepts content blocks on tool messages natively, other providers get a
    follow-up user message fallback, and models with supports_images=False get
    a text placeholder instead.
    """
    provider = (model.provider if model else "").lower()
    supports_images = model.supports_images if model else True

    out: list[BaseMessage] = []
    if system_prompt:
        out.append(SystemMessage(content=system_prompt))

    for msg in messages:
        if isinstance(msg, UserMessage):
            out.append(HumanMessage(content=_user_content_to_lc(msg.content)))
        elif isinstance(msg, AssistantMessage):
            text_parts: list[str] = []
            thinking_blocks: list[dict] = []
            tool_calls: list[dict] = []
            for block in msg.content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "thinking":
                    # Replay thinking blocks (Anthropic requires them, with
                    # signature, ahead of tool_use when thinking is enabled).
                    tb: dict = {"type": "thinking", "thinking": block["thinking"]}
                    if block.get("signature"):
                        tb["signature"] = block["signature"]
                    thinking_blocks.append(tb)
                elif block.get("type") == "toolCall":
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "name": block["name"],
                            "args": block["arguments"],
                        }
                    )
            content: str | list
            if thinking_blocks:
                content = list(thinking_blocks)
                if text_parts:
                    content.append({"type": "text", "text": "".join(text_parts)})
            else:
                content = "".join(text_parts) if text_parts else ""
            if tool_calls:
                ai = AIMessage(content=content, tool_calls=tool_calls)
            else:
                ai = AIMessage(content=content)
            out.append(ai)
        elif isinstance(msg, ToolResultMessage):
            text = " ".join(b["text"] for b in msg.content if b.get("type") == "text")
            image_blocks = [b for b in msg.content if b.get("type") == "image"]

            if image_blocks and not supports_images:
                suffix = "[image content removed]"
                out.append(
                    ToolMessage(
                        content=f"{text} {suffix}".strip(),
                        tool_call_id=msg.toolCallId,
                        name=msg.toolName,
                    )
                )
            elif image_blocks and provider == "anthropic":
                # Anthropic tool_result accepts content blocks natively.
                blocks: list = []
                if text:
                    blocks.append({"type": "text", "text": text})
                blocks.extend(_image_block_to_lc(b) for b in image_blocks)
                out.append(
                    ToolMessage(
                        content=blocks,
                        tool_call_id=msg.toolCallId,
                        name=msg.toolName,
                    )
                )
            elif image_blocks:
                # Other providers reject non-string tool message content; send
                # the image as a follow-up user message referencing the call.
                out.append(
                    ToolMessage(
                        content=text or "(image output attached below)",
                        tool_call_id=msg.toolCallId,
                        name=msg.toolName,
                    )
                )
                parts: list = [
                    {
                        "type": "text",
                        "text": f"Image output of tool call {msg.toolCallId} ({msg.toolName}):",
                    }
                ]
                parts.extend(_image_block_to_lc(b) for b in image_blocks)
                out.append(HumanMessage(content=parts))
            else:
                out.append(
                    ToolMessage(
                        content=text or "(empty)",
                        tool_call_id=msg.toolCallId,
                        name=msg.toolName,
                    )
                )
    return out


def agent_tool_to_lc_schema(tool: Any) -> dict | None:
    """Build JSON schema dict for bind_tools from AgentTool.parameters."""
    params = tool.parameters
    if isinstance(params, type):
        from pydantic import BaseModel

        if issubclass(params, BaseModel):
            return params.model_json_schema()
    if isinstance(params, dict):
        return params
    return None
