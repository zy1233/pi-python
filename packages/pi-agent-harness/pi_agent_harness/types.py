"""AgentHarness H1 types: errors, session entries, and storage protocols."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pi_agent_core.messages import AssistantMessage, ImageContent, TextContent
from pi_agent_core.types import AgentEvent, AgentMessage, Model, ThinkingLevel


class HarnessError(Exception):
    """Base exception with a stable machine-readable error code."""

    code: str

    def __init__(self, code: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause


FileErrorCode = Literal[
    "aborted",
    "not_found",
    "permission_denied",
    "not_directory",
    "is_directory",
    "invalid",
    "not_supported",
    "unknown",
]


class FileError(HarnessError):
    def __init__(
        self,
        code: FileErrorCode,
        message: str,
        path: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(code, message, cause)
        self.path = path


ExecutionErrorCode = Literal[
    "aborted",
    "timeout",
    "shell_unavailable",
    "spawn_error",
    "callback_error",
    "unknown",
]


class ExecutionError(HarnessError):
    def __init__(
        self, code: ExecutionErrorCode, message: str, cause: Exception | None = None
    ) -> None:
        super().__init__(code, message, cause)


CompactionErrorCode = Literal["aborted", "summarization_failed", "invalid_session", "unknown"]


class CompactionError(HarnessError):
    def __init__(
        self, code: CompactionErrorCode, message: str, cause: Exception | None = None
    ) -> None:
        super().__init__(code, message, cause)


BranchSummaryErrorCode = Literal["aborted", "summarization_failed", "invalid_session"]


class BranchSummaryError(HarnessError):
    def __init__(
        self, code: BranchSummaryErrorCode, message: str, cause: Exception | None = None
    ) -> None:
        super().__init__(code, message, cause)


SessionErrorCode = Literal[
    "not_found",
    "invalid_session",
    "invalid_entry",
    "invalid_fork_target",
    "storage",
    "unknown",
]


class SessionError(HarnessError):
    def __init__(
        self, code: SessionErrorCode, message: str, cause: Exception | None = None
    ) -> None:
        super().__init__(code, message, cause)


AgentHarnessErrorCode = Literal[
    "busy",
    "invalid_state",
    "invalid_argument",
    "session",
    "hook",
    "auth",
    "compaction",
    "branch_summary",
    "unknown",
]


class AgentHarnessError(HarnessError):
    def __init__(
        self, code: AgentHarnessErrorCode, message: str, cause: Exception | None = None
    ) -> None:
        super().__init__(code, message, cause)


def normalize_harness_error(
    error: Exception, fallback_code: AgentHarnessErrorCode
) -> AgentHarnessError:
    if isinstance(error, AgentHarnessError):
        return error
    if isinstance(error, SessionError):
        return AgentHarnessError("session", str(error), error)
    if isinstance(error, CompactionError):
        return AgentHarnessError("compaction", str(error), error)
    if isinstance(error, BranchSummaryError):
        return AgentHarnessError("branch_summary", str(error), error)
    return AgentHarnessError(fallback_code, str(error), error)


class FileInfo(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory", "symlink"]
    size: int
    mtimeMs: float


class ExecResult(BaseModel):
    stdout: str
    stderr: str
    exitCode: int


class FileSystem(Protocol):
    cwd: str

    async def read_text_file(self, path: str) -> str: ...

    async def read_text_lines(self, path: str, max_lines: int | None = None) -> list[str]: ...

    async def write_file(self, path: str, content: str | bytes) -> None: ...

    async def append_file(self, path: str, content: str | bytes) -> None: ...


class SessionTreeEntryBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    id: str
    parentId: str | None
    timestamp: str


class MessageEntry(SessionTreeEntryBase):
    type: Literal["message"] = "message"
    message: dict[str, Any]


class ThinkingLevelChangeEntry(SessionTreeEntryBase):
    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinkingLevel: str


class ModelChangeEntry(SessionTreeEntryBase):
    type: Literal["model_change"] = "model_change"
    provider: str
    modelId: str


class ActiveToolsChangeEntry(SessionTreeEntryBase):
    type: Literal["active_tools_change"] = "active_tools_change"
    activeToolNames: list[str]


class CompactionEntry(SessionTreeEntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str
    firstKeptEntryId: str
    tokensBefore: int
    details: Any = None
    fromHook: bool | None = None


class BranchSummaryEntry(SessionTreeEntryBase):
    type: Literal["branch_summary"] = "branch_summary"
    fromId: str
    summary: str
    details: Any = None
    fromHook: bool | None = None


class CustomEntry(SessionTreeEntryBase):
    type: Literal["custom"] = "custom"
    customType: str
    data: Any = None


class CustomMessageEntry(SessionTreeEntryBase):
    type: Literal["custom_message"] = "custom_message"
    customType: str
    content: str | list[dict[str, Any]]
    display: bool
    details: Any = None


class LabelEntry(SessionTreeEntryBase):
    type: Literal["label"] = "label"
    targetId: str
    label: str | None = None


class SessionInfoEntry(SessionTreeEntryBase):
    type: Literal["session_info"] = "session_info"
    name: str | None = None


class LeafEntry(SessionTreeEntryBase):
    type: Literal["leaf"] = "leaf"
    targetId: str | None


SessionTreeEntry = (
    MessageEntry
    | ThinkingLevelChangeEntry
    | ModelChangeEntry
    | ActiveToolsChangeEntry
    | CompactionEntry
    | BranchSummaryEntry
    | CustomEntry
    | CustomMessageEntry
    | LabelEntry
    | SessionInfoEntry
    | LeafEntry
)

SessionTreeEntryAdapter = TypeAdapter(Annotated[SessionTreeEntry, Field(discriminator="type")])


class SessionContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[AgentMessage]
    thinkingLevel: str = "off"
    model: dict[str, str] | None = None
    activeToolNames: list[str] | None = None


class SessionMetadata(BaseModel):
    id: str
    createdAt: str


class JsonlSessionMetadata(SessionMetadata):
    cwd: str
    path: str
    parentSessionPath: str | None = None


class PromptTemplate(BaseModel):
    name: str
    description: str | None = None
    content: str


class Skill(BaseModel):
    name: str
    description: str
    content: str
    filePath: str
    disableModelInvocation: bool = False


class AgentHarnessResources(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    promptTemplates: list[PromptTemplate] | None = None
    skills: list[Skill] | None = None


class AgentHarnessStreamOptions(BaseModel):
    timeoutMs: int | None = None
    maxRetries: int | None = None
    maxRetryDelayMs: int | None = None
    headers: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None


class AgentHarnessStreamOptionsPatch(BaseModel):
    timeoutMs: int | None = None
    maxRetries: int | None = None
    maxRetryDelayMs: int | None = None
    headers: dict[str, str | None] | None = None
    metadata: dict[str, Any | None] | None = None


class CompactionSettings(BaseModel):
    enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000
    auto_compact: bool = False


class CompactionPreparation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    entries: list[SessionTreeEntry]
    messages: list[AgentMessage]
    keptMessages: list[AgentMessage]
    firstKeptEntryId: str
    tokensBefore: int
    splitTurnSummary: str | None = None
    previousSummary: str | None = None
    previousDetails: Any = None


class CompactionResult(BaseModel):
    summary: str
    firstKeptEntryId: str
    tokensBefore: int
    details: Any = None
    fromHook: bool = False


class NavigateTreeResult(BaseModel):
    targetId: str | None
    leafId: str | None
    editorText: str | None = None
    summary: str | None = None
    branchSummaryEntryId: str | None = None


class QueueUpdateEvent(BaseModel):
    type: Literal["queue_update"] = "queue_update"
    steer: list[AgentMessage]
    followUp: list[AgentMessage]
    nextTurn: list[AgentMessage]


class SavePointEvent(BaseModel):
    type: Literal["save_point"] = "save_point"
    hadPendingMutations: bool


class AbortEvent(BaseModel):
    type: Literal["abort"] = "abort"
    clearedSteer: list[AgentMessage]
    clearedFollowUp: list[AgentMessage]


class SettledEvent(BaseModel):
    type: Literal["settled"] = "settled"
    nextTurnCount: int


class BeforeAgentStartEvent(BaseModel):
    type: Literal["before_agent_start"] = "before_agent_start"
    prompt: str
    images: list[ImageContent] | None = None
    systemPrompt: str
    resources: AgentHarnessResources


class ContextEvent(BaseModel):
    type: Literal["context"] = "context"
    messages: list[AgentMessage]


class BeforeProviderRequestEvent(BaseModel):
    type: Literal["before_provider_request"] = "before_provider_request"
    model: Model
    sessionId: str
    streamOptions: AgentHarnessStreamOptions


class BeforeProviderPayloadEvent(BaseModel):
    type: Literal["before_provider_payload"] = "before_provider_payload"
    model: Model
    payload: Any


class AfterProviderResponseEvent(BaseModel):
    type: Literal["after_provider_response"] = "after_provider_response"
    status: int = 0
    headers: dict[str, str] = Field(default_factory=dict)
    message: AssistantMessage | None = None


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    toolCallId: str
    toolName: str
    input: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    toolCallId: str
    toolName: str
    input: dict[str, Any]
    content: list[TextContent | ImageContent]
    details: Any = None
    isError: bool = False


class ModelUpdateEvent(BaseModel):
    type: Literal["model_update"] = "model_update"
    model: Model
    previousModel: Model | None = None
    source: Literal["set", "restore"] = "set"


class ThinkingLevelUpdateEvent(BaseModel):
    type: Literal["thinking_level_update"] = "thinking_level_update"
    level: ThinkingLevel
    previousLevel: ThinkingLevel


class ToolsUpdateEvent(BaseModel):
    type: Literal["tools_update"] = "tools_update"
    toolNames: list[str]
    previousToolNames: list[str]
    activeToolNames: list[str]
    previousActiveToolNames: list[str]
    source: Literal["set", "restore"] = "set"


class ResourcesUpdateEvent(BaseModel):
    type: Literal["resources_update"] = "resources_update"
    resources: AgentHarnessResources
    previousResources: AgentHarnessResources


class SessionBeforeCompactEvent(BaseModel):
    type: Literal["session_before_compact"] = "session_before_compact"
    preparation: CompactionPreparation
    customInstructions: str | None = None


class SessionCompactEvent(BaseModel):
    type: Literal["session_compact"] = "session_compact"
    result: CompactionResult


class SessionBeforeTreeEvent(BaseModel):
    type: Literal["session_before_tree"] = "session_before_tree"
    targetId: str | None
    oldLeafId: str | None
    summarize: bool = False
    customInstructions: str | None = None
    label: str | None = None


class SessionTreeEvent(BaseModel):
    type: Literal["session_tree"] = "session_tree"
    result: NavigateTreeResult


AgentHarnessOwnEvent = (
    QueueUpdateEvent
    | SavePointEvent
    | AbortEvent
    | SettledEvent
    | BeforeAgentStartEvent
    | ContextEvent
    | BeforeProviderRequestEvent
    | BeforeProviderPayloadEvent
    | AfterProviderResponseEvent
    | ToolCallEvent
    | ToolResultEvent
    | ModelUpdateEvent
    | ThinkingLevelUpdateEvent
    | ToolsUpdateEvent
    | ResourcesUpdateEvent
    | SessionBeforeCompactEvent
    | SessionCompactEvent
    | SessionBeforeTreeEvent
    | SessionTreeEvent
)

AgentHarnessEvent = AgentEvent | AgentHarnessOwnEvent
AgentHarnessHandler = Callable[[AgentHarnessEvent, Any | None], Any]


TMetadata = TypeVar("TMetadata", bound=SessionMetadata)


class SessionStorage(Protocol):
    async def get_metadata(self) -> SessionMetadata: ...

    async def get_leaf_id(self) -> str | None: ...

    async def set_leaf_id(self, leaf_id: str | None) -> None: ...

    async def create_entry_id(self) -> str: ...

    async def append_entry(self, entry: SessionTreeEntry) -> None: ...

    async def get_entry(self, id: str) -> SessionTreeEntry | None: ...

    async def find_entries(self, type_: str) -> list[SessionTreeEntry]: ...

    async def get_label(self, id: str) -> str | None: ...

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]: ...

    async def get_entries(self) -> list[SessionTreeEntry]: ...


class SessionRepo(Protocol[TMetadata]):
    async def create(self, options: dict[str, Any] | None = None) -> Any: ...

    async def open(self, metadata: TMetadata) -> Any: ...

    async def list(self, options: dict[str, Any] | None = None) -> list[TMetadata]: ...

    async def delete(self, metadata: TMetadata) -> None: ...

    async def fork(self, source: TMetadata, options: dict[str, Any] | None = None) -> Any: ...
