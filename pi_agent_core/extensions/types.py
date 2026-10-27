"""Extension system types (Phase 7 — ExtensionAPI)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from pi_agent_core.types import AgentToolResult, ToolAnnotations, ToolExecutionMode

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

ExecuteFn = Callable[..., Awaitable[AgentToolResult] | AgentToolResult]
"""The ``execute`` of an extension tool. It can be sync or async, and returns an
``AgentToolResult``.

The harness always calls it with four positional arguments (audit F7-08):
``execute(tool_call_id, params, signal, on_update)``.

- ``tool_call_id``: the id of the tool call (``str``).
- ``params``: the validated arguments. An instance of ``parameters`` when that is a Pydantic
  model, else the argument ``dict``.
- ``signal``: the abort signal of the run, or ``None``.
- ``on_update``: a callback that takes a partial ``AgentToolResult`` (progress), or ``None``.

A function that takes fewer positional arguments fails with ``TypeError`` when the model calls
the tool, not when it is registered. Upstream TS tools often take two. Give the last two a default
(``signal=None, on_update=None``) when the tool does not use them.
"""

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


def validate_command_name(name: str, *, extension: str | None = None) -> None:
    """Raise ``ValueError`` unless *name* is a slash-command name that can be typed and found.

    The harness takes the name from the text after the ``/``, up to the first white space
    (``AgentHarness._try_slash_dispatch``). So a name that is empty, that holds white space, or
    that starts with ``/`` can never be matched, yet the clients are told about it as a
    command. Fail at registration instead, where the culprit is known.
    """
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("/")
        or any(char.isspace() for char in name)
    ):
        origin = f" from extension {extension!r}" if extension else ""
        raise ValueError(
            f"Invalid command name {name!r}{origin}: a command name must not be empty, must "
            "not start with '/' and must not hold white space; the harness finds a command by "
            "the text between the '/' and the first white space."
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
# MCP servers the client passed (data only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientMcpServer:
    """One MCP server that the client (an editor, say) asked the agent to connect.

    This is data and nothing more. Core, harness and CLI only carry it; an extension acts on it
    (``pi-mcp-py`` does). They hold no MCP client code (``docs/specs/2026-10-23-mcp-extension-
    design.md``).

    ``command``, ``args``, ``env``, ``url`` and ``headers`` often hold tokens, so they stay out
    of ``repr``: a log line with the whole object is safe. ``env`` and ``headers`` are tuples of
    ``(name, value)`` pairs in the order the client sent them, so the object is immutable and
    hashable. The fields that do not apply to a transport stay empty.
    """

    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str = field(default="", repr=False)
    args: tuple[str, ...] = field(default=(), repr=False)
    env: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    url: str = field(default="", repr=False)
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)


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
