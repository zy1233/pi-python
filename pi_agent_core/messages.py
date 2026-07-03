"""Canonical message types aligned with pi-ai."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

# pydantic requires the typing_extensions variants on Python < 3.12 to build
# schemas from TypedDicts (typing.TypedDict lacks the needed introspection);
# NotRequired must come from the same module as TypedDict.
from typing_extensions import NotRequired, TypedDict  # noqa: UP035


def _now_ms() -> int:
    return int(time.time() * 1000)


class TextContent(TypedDict):
    type: Literal["text"]
    text: str


class ImageContent(TypedDict):
    type: Literal["image"]
    data: str
    mimeType: str


class ThinkingContent(TypedDict):
    type: Literal["thinking"]
    thinking: str
    # Provider signature (Anthropic) required to replay thinking blocks alongside
    # tool use in follow-up requests.
    signature: NotRequired[str]


class ToolCallContent(TypedDict):
    type: Literal["toolCall"]
    id: str
    name: str
    arguments: dict[str, Any]


ContentBlock = TextContent | ImageContent | ThinkingContent | ToolCallContent

StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]


class UsageCost(BaseModel):
    input: float = 0
    output: float = 0
    cacheRead: float = 0
    cacheWrite: float = 0
    total: float = 0


class Usage(BaseModel):
    input: int = 0
    output: int = 0
    cacheRead: int = 0
    cacheWrite: int = 0
    totalTokens: int = 0
    reasoningTokens: int = 0
    cost: UsageCost = Field(default_factory=UsageCost)


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[TextContent | ImageContent]
    timestamp: int = Field(default_factory=_now_ms)


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock]
    api: str = "langchain"
    provider: str = "unknown"
    model: str = "unknown"
    usage: Usage = Field(default_factory=Usage)
    stopReason: StopReason = "stop"
    errorMessage: str | None = None
    timestamp: int = Field(default_factory=_now_ms)


class ToolResultMessage(BaseModel):
    role: Literal["toolResult"] = "toolResult"
    toolCallId: str
    toolName: str
    content: list[TextContent | ImageContent]
    details: Any = None
    isError: bool = False
    timestamp: int = Field(default_factory=_now_ms)


Message = UserMessage | AssistantMessage | ToolResultMessage

AgentToolCall = ToolCallContent
