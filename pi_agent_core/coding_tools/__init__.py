"""Built-in coding tools (port of pi ``packages/coding-agent/src/core/tools``).

Design: ``docs/superpowers/specs/2026-07-03-p6-tool-ecosystem-design.md``.
This batch ships the shared infrastructure (truncation, path helpers, per-file
mutation mutex); the tool factories (``create_coding_tools`` /
``create_read_only_tools`` / per-tool ``create_*_tool``) land with the tools.
"""

from pi_agent_core.coding_tools.mutation_queue import with_file_mutation_queue
from pi_agent_core.coding_tools.path_utils import (
    detect_image_mime,
    glob_to_regex,
    resolve_to_cwd,
)
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

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "TruncationResult",
    "detect_image_mime",
    "format_size",
    "glob_to_regex",
    "resolve_to_cwd",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
    "with_file_mutation_queue",
]
