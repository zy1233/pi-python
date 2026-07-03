"""Built-in coding tools (port of pi ``packages/coding-agent/src/core/tools``).

Design: ``docs/superpowers/specs/2026-07-03-p6-tool-ecosystem-design.md``.
Shipped so far: shared infrastructure + the filesystem tools
(read/write/ls/edit). Still to come: bash, grep, find, and the group
factories (``create_coding_tools`` / ``create_read_only_tools``).
"""

from pi_agent_core.coding_tools.edit import create_edit_tool
from pi_agent_core.coding_tools.ls import create_ls_tool
from pi_agent_core.coding_tools.mutation_queue import with_file_mutation_queue
from pi_agent_core.coding_tools.path_utils import (
    detect_image_mime,
    glob_to_regex,
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

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "TruncationResult",
    "create_edit_tool",
    "create_ls_tool",
    "create_read_tool",
    "create_write_tool",
    "detect_image_mime",
    "format_size",
    "glob_to_regex",
    "resolve_to_cwd",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
    "with_file_mutation_queue",
]
