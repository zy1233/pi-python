"""Agent runtime types (ported from pi-agent-core types.ts)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from pi_agent_core.event_stream import AssistantMessageEventStream

from pydantic import BaseModel, Field

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


class MaxTurnsExceededError(RuntimeError):
    """Raised when the agent loop exceeds AgentLoopConfig.max_turns.

    Mirrors OpenAI Agents SDK MaxTurnsExceeded semantics: hitting the guard is an
    abnormal condition the caller must notice, not a silent graceful stop. The
    Agent wrapper converts it into an error-stop assistant message and sets
    `Agent.error_message`.
    """


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
    # Custom endpoint for OpenAI-compatible providers (mirrors pi's baseUrl),
    # e.g. SiliconFlow/DeepSeek/vLLM gateways.
    base_url: str | None = None

    @property
    def id(self) -> str:
        return self.model_id


class ContextBudget(BaseModel):
    """Token budget signal for the context window (audit C2 / #4 core half).

    Derived from the previous LLM call's usage: `used_tokens` is what occupied
    the window on that call (Usage follows LangChain-standardized metadata, so
    `input` already includes cache read/write tokens). Harness-layer compaction
    strategies consume this via the before_llm_call hook; core only produces
    the signal.
    """

    used_tokens: int = 0
    context_window: int = 0

    @property
    def fraction(self) -> float:
        """Fraction of the context window used (0.0 when unknown)."""
        if self.context_window <= 0:
            return 0.0
        return self.used_tokens / self.context_window

    @classmethod
    def from_usage(cls, usage: Usage, model: Model) -> ContextBudget:
        used = usage.totalTokens or (usage.input + usage.output)
        return cls(used_tokens=used, context_window=model.context_window)


class AgentToolResult(BaseModel):
    content: list[TextContent | ImageContent]
    details: Any = None
    terminate: bool | None = None


# Synchronous callback; returns an optional awaitable that resolves once the
# update event has been delivered (awaiting it is not required).
AgentToolUpdateCallback = Callable[[AgentToolResult], Any]


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
    # Stream-level retries before the first token (transient errors only); the
    # underlying provider SDK performs its own fast request-level retries on top.
    max_retries: int = 2
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    # Observability hooks (mirror pi's onPayload/onResponse). on_payload receives
    # the outgoing request description before the LLM call; on_response receives
    # the final AssistantMessage. Sync or async; exceptions propagate as error
    # events (not swallowed), matching pi.
    on_payload: Callable[[dict[str, Any]], Any] | None = None
    on_response: Callable[[AssistantMessage], Any] | None = None
    # Structured output (audit #7): a JSON schema dict or pydantic BaseModel
    # subclass. Providers with response_format get it natively (json_schema);
    # others get schema instructions appended to the system prompt. The final
    # text is parsed into AssistantMessage.structured_output.
    response_schema: dict[str, Any] | type | None = None


ConvertToLlmFn = Callable[[list[AgentMessage]], list[Message] | Awaitable[list[Message]]]
TransformContextFn = Callable[
    [list[AgentMessage], Any | None], list[AgentMessage] | Awaitable[list[AgentMessage]]
]


@dataclass
class AgentLoopConfig:
    model: Model
    convert_to_llm: ConvertToLlmFn
    transform_context: TransformContextFn | None = None
    get_api_key: Callable[[str], str | Awaitable[str | None] | None] | None = None
    should_stop_after_turn: (
        Callable[[ShouldStopAfterTurnContext], bool | Awaitable[bool]] | None
    ) = None
    prepare_next_turn: (
        Callable[
            [ShouldStopAfterTurnContext],
            AgentLoopTurnUpdate | Awaitable[AgentLoopTurnUpdate | None] | None,
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
            BeforeToolCallResult | Awaitable[BeforeToolCallResult | None] | None,
        ]
        | None
    ) = None
    after_tool_call: (
        Callable[
            [AfterToolCallContext, Any | None],
            AfterToolCallResult | Awaitable[AfterToolCallResult | None] | None,
        ]
        | None
    ) = None
    api_key: str | None = None
    signal: Any | None = None
    thinking_level: ThinkingLevel | None = None
    cost_calculator: CostCalculator | None = None
    # Runaway protection: max total turns per run (raises MaxTurnsExceededError)
    # and per-tool-call wall-clock timeout in seconds (times out into an error
    # tool result the LLM can see).
    max_turns: int | None = None
    tool_timeout: float | None = None
    # Overrides StreamOptions.max_retries when set.
    max_retries: int | None = None
    # Observability hooks forwarded into StreamOptions.
    on_payload: Callable[[dict[str, Any]], Any] | None = None
    on_response: Callable[[AssistantMessage], Any] | None = None
    # Lifecycle guardrail hooks (audit #5, OpenAI Agents SDK guardrail parity).
    # before_llm_call fires before every LLM call with a ContextBudget signal
    # derived from the previous call's usage (None on the first call);
    # returning an AgentContext durably replaces the loop context — this is the
    # compaction hook point (unlike transform_context, which re-runs on the
    # full history every call and never persists).
    before_llm_call: (
        Callable[
            [AgentContext, ContextBudget | None],
            AgentContext | Awaitable[AgentContext | None] | None,
        ]
        | None
    ) = None
    # after_llm_call fires after each completed assistant message, before tool
    # execution; raise to abort the run (guardrail tripwire semantics).
    after_llm_call: Callable[[AgentContext, AssistantMessage], Any] | None = None
    # on_agent_end fires with the run's new messages just before agent_end.
    on_agent_end: Callable[[list[AgentMessage]], Any] | None = None
    # Structured output schema forwarded into StreamOptions (audit #7).
    response_schema: dict[str, Any] | type | None = None


# --- Assistant stream events ---


class AssistantMessageEventBase(BaseModel):
    partial: AssistantMessage


class StartEvent(AssistantMessageEventBase):
    type: Literal["start"] = "start"


class TextStartEvent(AssistantMessageEventBase):
    type: Literal["text_start"] = "text_start"
    content_index: int = 0


class TextDeltaEvent(AssistantMessageEventBase):
    type: Literal["text_delta"] = "text_delta"
    delta: str
    content_index: int = 0


class TextEndEvent(AssistantMessageEventBase):
    type: Literal["text_end"] = "text_end"
    content: str = ""
    content_index: int = 0


class ThinkingStartEvent(AssistantMessageEventBase):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int = 0


class ThinkingDeltaEvent(AssistantMessageEventBase):
    type: Literal["thinking_delta"] = "thinking_delta"
    delta: str
    content_index: int = 0


class ThinkingEndEvent(AssistantMessageEventBase):
    type: Literal["thinking_end"] = "thinking_end"
    content: str = ""
    content_index: int = 0


class ToolCallStartEvent(AssistantMessageEventBase):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int = 0
    tool_call_index: int = 0


class ToolCallDeltaEvent(AssistantMessageEventBase):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    delta: str
    content_index: int
    tool_call_index: int = 0


class ToolCallEndEvent(AssistantMessageEventBase):
    type: Literal["toolcall_end"] = "toolcall_end"
    tool_call: AgentToolCall | None = None
    content_index: int = 0
    tool_call_index: int = 0


class DoneEvent(AssistantMessageEventBase):
    type: Literal["done"] = "done"
    reason: Literal["stop", "toolUse"] = "stop"


class ErrorEvent(AssistantMessageEventBase):
    type: Literal["error"] = "error"
    reason: Literal["error", "aborted"] = "error"
    error_message: str | None = None


AssistantMessageEvent = (
    StartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | DoneEvent
    | ErrorEvent
)


# --- Agent events ---


class AgentEventBase(BaseModel):
    """Correlation fields stamped by the loop (observability, audit #6).

    run_id groups every event of one run_agent_loop invocation; turn_id is the
    1-based turn counter (0 for pre-turn events like agent_start).
    """

    run_id: str = ""
    turn_id: int = 0


class AgentStartEvent(AgentEventBase):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(AgentEventBase):
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]


class TurnStartEvent(AgentEventBase):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(AgentEventBase):
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: list[ToolResultMessage] = Field(default_factory=list)


class MessageStartEvent(AgentEventBase):
    type: Literal["message_start"] = "message_start"
    message: AgentMessage


class MessageUpdateEvent(AgentEventBase):
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent


class MessageEndEvent(AgentEventBase):
    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(AgentEventBase):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    args: Any


class ToolExecutionUpdateEvent(AgentEventBase):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class ToolExecutionEndEvent(AgentEventBase):
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

AgentEventSink = Callable[[AgentEvent], Awaitable[None] | None]

StreamFn = Callable[
    [Model, LlmContext, StreamOptions],
    "AssistantMessageEventStream | Awaitable[AssistantMessageEventStream]",
]
