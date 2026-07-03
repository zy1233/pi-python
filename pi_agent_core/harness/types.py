"""AgentHarness H1 types: errors, session entries, and storage protocols."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pi_agent_core.types import AgentMessage


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
