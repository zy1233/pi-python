"""Extension system types (Phase 7 — ExtensionAPI)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from pi_agent_core.types import AgentToolResult, ToolAnnotations, ToolExecutionMode

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

ExecuteFn = Callable[..., Awaitable[AgentToolResult] | AgentToolResult]

TOOL_NAME_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,128}")
"""What model providers accept as a function name (OpenAI, DeepSeek and others)."""


def validate_tool_name(name: str, *, extension: str | None = None) -> None:
    """Raise ``ValueError`` unless *name* is a tool name every provider accepts.

    A name outside ``[a-zA-Z0-9_-]{1,128}`` is not rejected locally but by the provider,
    on *every* request from then on, so the whole session stops working. Fail at
    registration instead, where the culprit is known.
    """
    if not isinstance(name, str) or TOOL_NAME_PATTERN.fullmatch(name) is None:
        origin = f" from extension {extension!r}" if extension else ""
        raise ValueError(
            f"Invalid tool name {name!r}{origin}: a tool name must match "
            "[a-zA-Z0-9_-]{1,128}; model providers reject any other name on every request."
        )


@dataclass
class ToolDefinition:
    """Declarative tool definition passed to ``ExtensionAPI.register_tool``.

    Mirrors upstream pi's ``registerTool()`` shape.  Converted internally
    to a ``CodingTool`` / ``SimpleTool`` for the agent loop.
    """

    name: str
    description: str
    parameters: type[BaseModel] | dict[str, Any]
    execute: ExecuteFn
    label: str | None = None
    prompt_snippet: str | None = None
    prompt_guidelines: list[str] = field(default_factory=list)
    execution_mode: ToolExecutionMode | None = None
    prepare_arguments: Callable[[Any], Any] | None = None
    annotations: ToolAnnotations | None = None
    """MCP-style hints about the tool (``readOnlyHint`` ...). In the CLI's ``ask`` mode a
    call is asked about unless the tool declares ``readOnlyHint: true`` (or
    ``destructiveHint: false`` with ``openWorldHint: false``); no annotations, no exemption."""


# ---------------------------------------------------------------------------
# Command definitions
# ---------------------------------------------------------------------------

CommandHandler = Callable[..., Any]


@dataclass
class CommandDef:
    """A slash-command registered by an extension.

    When ``passthrough`` is True the command is advertised to clients (for
    autocomplete) but **not** intercepted by the harness — the original
    ``/command args`` text is forwarded to the LLM as a regular prompt so
    the model can invoke the corresponding tool.
    """

    name: str
    description: str
    handler: CommandHandler
    extension_name: str | None = None
    passthrough: bool = False


# ---------------------------------------------------------------------------
# Event handler type
# ---------------------------------------------------------------------------

EventHandler = Callable[..., Any]


@dataclass
class EventRegistration:
    """An event subscription registered by an extension."""

    event: str
    handler: EventHandler
    extension_name: str | None = None


# ---------------------------------------------------------------------------
# Extension metadata
# ---------------------------------------------------------------------------


@dataclass
class ExtensionMeta:
    """Metadata about a loaded extension."""

    name: str
    path: str | None = None
    source: str = "unknown"  # "entry_point" | "directory" | "programmatic"


# ---------------------------------------------------------------------------
# Tool info (read-only view returned by get_all_tools)
# ---------------------------------------------------------------------------


@dataclass
class ToolInfo:
    """Read-only tool descriptor returned by ``ExtensionAPI.get_all_tools``."""

    name: str
    description: str
    source: str  # "builtin" | "extension"
    prompt_snippet: str | None = None
    prompt_guidelines: list[str] = field(default_factory=list)
