"""Built-in coding tools (port of pi ``packages/coding-agent/src/core/tools``).

Design: ``docs/specs/2026-07-03-p6-tool-ecosystem-design.md``.
All seven tools (read/bash/edit/write/grep/find/ls) plus the shared
infrastructure are implemented.

Factory usage (mirrors pi's ``createAgentSession({ tools })`` consumption):

    from pi_agent_core.coding_tools import create_coding_tools
    tools = create_coding_tools("/path/to/project")
"""

from collections.abc import Callable
from typing import Any, Literal

from pi_agent_core.coding_tools.bash import create_bash_tool
from pi_agent_core.coding_tools.edit import create_edit_tool
from pi_agent_core.coding_tools.find import create_find_tool
from pi_agent_core.coding_tools.grep import create_grep_tool
from pi_agent_core.coding_tools.ls import create_ls_tool
from pi_agent_core.coding_tools.mutation_queue import with_file_mutation_queue
from pi_agent_core.coding_tools.path_utils import (
    DEFAULT_IGNORE_DIRS,
    compile_glob,
    detect_image_mime,
    glob_to_regex,
    normalize_host_path,
    resolve_to_cwd,
)
from pi_agent_core.coding_tools.read import create_read_tool
from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    GREP_MAX_LINE_LENGTH,
    TruncationResult,
    format_size,
    truncate_head,
    truncate_line,
    truncate_tail,
)
from pi_agent_core.coding_tools.write import create_write_tool
from pi_agent_core.types import AgentTool

ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]

ALL_TOOL_NAMES: frozenset[ToolName] = frozenset(
    ("read", "bash", "edit", "write", "grep", "find", "ls")
)

# pi's default four: full file operations + command execution.
CODING_TOOL_NAMES: tuple[ToolName, ...] = ("read", "bash", "edit", "write")
# pi's read-only mode: inspection with a no-modification guarantee.
READ_ONLY_TOOL_NAMES: tuple[ToolName, ...] = ("read", "grep", "find", "ls")

_FACTORIES: dict[str, Callable[..., AgentTool]] = {
    "read": create_read_tool,
    "bash": create_bash_tool,
    "edit": create_edit_tool,
    "write": create_write_tool,
    "grep": create_grep_tool,
    "find": create_find_tool,
    "ls": create_ls_tool,
}


def create_tool(name: ToolName, cwd: str, **options: Any) -> AgentTool:
    """Build one built-in tool by name, bound to *cwd*.

    ``**options`` forwards to the per-tool factory (e.g. grep's
    ``use_fallback``, bash's ``shell_path``); unknown names raise
    ``ValueError``.
    """
    factory = _FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown tool name: {name!r}. Valid names: {sorted(ALL_TOOL_NAMES)}")
    return factory(cwd, **options)


def create_coding_tools(cwd: str) -> list[AgentTool]:
    """pi's default group (read/bash/edit/write): full file ops + command execution."""
    return [create_tool(name, cwd) for name in CODING_TOOL_NAMES]


def create_read_only_tools(cwd: str) -> list[AgentTool]:
    """pi's read-only group (read/grep/find/ls): inspection without modification."""
    return [create_tool(name, cwd) for name in READ_ONLY_TOOL_NAMES]


def create_all_tools(cwd: str) -> dict[ToolName, AgentTool]:
    """All built-in tools keyed by name."""
    return {name: create_tool(name, cwd) for name in sorted(ALL_TOOL_NAMES)}


__all__ = [
    "ALL_TOOL_NAMES",
    "CODING_TOOL_NAMES",
    "DEFAULT_IGNORE_DIRS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "READ_ONLY_TOOL_NAMES",
    "ToolName",
    "TruncationResult",
    "compile_glob",
    "create_all_tools",
    "create_bash_tool",
    "create_coding_tools",
    "create_edit_tool",
    "create_find_tool",
    "create_grep_tool",
    "create_ls_tool",
    "create_read_only_tools",
    "create_read_tool",
    "create_tool",
    "create_write_tool",
    "detect_image_mime",
    "format_size",
    "glob_to_regex",
    "normalize_host_path",
    "resolve_to_cwd",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
    "with_file_mutation_queue",
]
