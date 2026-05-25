"""Agent runtime types (ported from pi-agent-core types.ts)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from pi_agent_core.event_stream import AssistantMessageEventStream

from pydantic import BaseModel

from pi_agent_core.messages import (
    AgentToolCall,
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolResultMessage,
    Usage,
    UsageCost,
)

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
ToolExecutionMode = Literal["sequential", "parallel"]
QueueMode = Literal["all", "one-at-a-time"]

# Extensible custom messages — apps can subclass or use TypedDict merging patterns
CustomAgentMessages = dict[str, Any]
AgentMessage = Message | Any


@dataclass
class Model:
    provider: str
    model_id: str
    api: str = "langchain"
    context_window: int = 128_000
    supports_images: bool = True
    reasoning: bool = False

    @property
    def id(self) -> str:
        return self.model_id


class AgentToolResult(BaseModel):
    content: list[TextContent | ImageContent]
    details: Any = None
    terminate: bool | None = None


AgentToolUpdateCallback = Callable[[AgentToolResult], None]


class AgentTool(Protocol):
    name: str
    description: str
    label: str
    parameters: type[BaseModel] | dict[str, Any]
    execution_mode: ToolExecutionMode | None

    def prepare_arguments(self, args: Any) -> Any: ...

    async def execute(
        self,
        tool_call_id: str,
        params: Any,
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult: ...


@dataclass
class AgentContext:
    system_prompt: str
    messages: list[AgentMessage]
    tools: list[Any] | None = None


@dataclass
class LlmContext:
    """Context passed to StreamFn (LLM boundary)."""

    system_prompt: str | None
    messages: list[Message]
    tools: list[Any] | None = None


class BeforeToolCallResult(BaseModel):
    block: bool | None = None
    reason: str | None = None


class AfterToolCallResult(BaseModel):
    content: list[TextContent | ImageContent] | None = None
    details: Any | None = None
    is_error: bool | None = None
    terminate: bool | None = None


@dataclass
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    context: AgentContext


@dataclass
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    result: AgentToolResult
    is_error: bool
    context: AgentContext


@dataclass
class ShouldStopAfterTurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: AgentContext
    new_messages: list[AgentMessage]


@dataclass
class AgentLoopTurnUpdate:
    context: AgentContext | None = None
    model: Model | None = None
    thinking_level: ThinkingLevel | None = None


CostCalculator = Callable[[Usage, Model], UsageCost]


@dataclass
class StreamOptions:
    api_key: str | None = None
    signal: Any | None = None
    session_id: str | None = None
    reasoning: ThinkingLevel | None = None
    cost_calculator: CostCalculator | None = None


ConvertToLlmFn = Callable[[list[AgentMessage]], list[Message] | Awaitable[list[Message]]]
TransformContextFn = Callable[
    [list[AgentMessage], Any | None], list[AgentMessage] | Awaitable[list[AgentMessage]]
]


@dataclass
class AgentLoopConfig:
    model: Model
    convert_to_llm: ConvertToLlmFn
    transform_context: TransformContextFn | None = None
    get_api_key: Callable[[str], str | None | Awaitable[str | None]] | None = None
    should_stop_after_turn: (
        Callable[[ShouldStopAfterTurnContext], bool | Awaitable[bool]] | None
    ) = None
    prepare_next_turn: (
        Callable[
            [ShouldStopAfterTurnContext],
            AgentLoopTurnUpdate | None | Awaitable[AgentLoopTurnUpdate | None],
        ]
        | None
    ) = None
    get_steering_messages: (
        Callable[[], list[AgentMessage] | Awaitable[list[AgentMessage]]] | None
    ) = None
    get_follow_up_messages: (
        Callable[[], list[AgentMessage] | Awaitable[list[AgentMessage]]] | None
    ) = None
    tool_execution: ToolExecutionMode = "parallel"
    before_tool_call: (
        Callable[
            [BeforeToolCallContext, Any | None],
            BeforeToolCallResult | None | Awaitable[BeforeToolCallResult | None],
        ]
        | None
    ) = None
    after_tool_call: (
        Callable[
            [AfterToolCallContext, Any | None],
            AfterToolCallResult | None | Awaitable[AfterToolCallResult | None],
        ]
        | None
    ) = None
    api_key: str | None = None
    signal: Any | None = None
    thinking_level: ThinkingLevel | None = None
    cost_calculator: CostCalculator | None = None


# --- Assistant stream events ---


class AssistantMessageEventBase(BaseModel):
    partial: AssistantMessage


class StartEvent(AssistantMessageEventBase):
    type: Literal["start"] = "start"


class TextDeltaEvent(AssistantMessageEventBase):
    type: Literal["text_delta"] = "text_delta"
    delta: str
    content_index: int = 0


class ToolCallDeltaEvent(AssistantMessageEventBase):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    delta: str
    content_index: int
    tool_call_index: int = 0


class DoneEvent(AssistantMessageEventBase):
    type: Literal["done"] = "done"
    reason: Literal["stop", "toolUse"] = "stop"


class ErrorEvent(AssistantMessageEventBase):
    type: Literal["error"] = "error"
    reason: Literal["error", "aborted"] = "error"
    error_message: str | None = None


AssistantMessageEvent = StartEvent | TextDeltaEvent | ToolCallDeltaEvent | DoneEvent | ErrorEvent


# --- Agent events ---


class AgentStartEvent(BaseModel):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(BaseModel):
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]


class TurnStartEvent(BaseModel):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(BaseModel):
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: list[ToolResultMessage] = field(default_factory=list)


class MessageStartEvent(BaseModel):
    type: Literal["message_start"] = "message_start"
    message: AgentMessage


class MessageUpdateEvent(BaseModel):
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent


class MessageEndEvent(BaseModel):
    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(BaseModel):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    args: Any


class ToolExecutionUpdateEvent(BaseModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class ToolExecutionEndEvent(BaseModel):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool


AgentEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
)

AgentEventSink = Callable[[AgentEvent], None | Awaitable[None]]

StreamFn = Callable[
    [Model, LlmContext, StreamOptions],
    "AssistantMessageEventStream | Awaitable[AssistantMessageEventStream]",
]
