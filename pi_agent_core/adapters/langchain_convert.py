"""Convert pi messages to LangChain BaseMessage."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from pi_agent_core.messages import AssistantMessage, Message, ToolResultMessage, UserMessage
from pi_agent_core.types import AgentMessage


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    result: list[Message] = []
    for m in messages:
        role = getattr(m, "role", None)
        if role in ("user", "assistant", "toolResult"):
            result.append(m)
    return result


def _user_content_to_lc(content: str | list) -> str | list:
    if isinstance(content, str):
        return content
    parts: list = []
    for block in content:
        if block.get("type") == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block['mimeType']};base64,{block['data']}"},
                }
            )
    return parts if parts else ""


def convert_to_langchain(
    messages: list[Message],
    system_prompt: str | None = None,
) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    if system_prompt:
        out.append(SystemMessage(content=system_prompt))

    for msg in messages:
        if isinstance(msg, UserMessage):
            out.append(HumanMessage(content=_user_content_to_lc(msg.content)))
        elif isinstance(msg, AssistantMessage):
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in msg.content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "toolCall":
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "name": block["name"],
                            "args": block["arguments"],
                        }
                    )
            ai = AIMessage(content="".join(text_parts) if text_parts else "")
            if tool_calls:
                ai = AIMessage(content=ai.content, tool_calls=tool_calls)
            out.append(ai)
        elif isinstance(msg, ToolResultMessage):
            text = " ".join(b["text"] for b in msg.content if b.get("type") == "text")
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
