"""find tool (port of pi ``find.ts``; pure-Python walk instead of fd).

Declared divergences from pi (spec §4.6 / §7): no fd binary and no automatic
download — a stdlib ``os.walk`` with the ``DEFAULT_IGNORE_DIRS`` prune list
replaces fd's .gitignore awareness. Glob semantics mirror pi's fd invocation:
basename match for plain patterns, any-depth relative-path match for patterns
containing ``/`` (see ``path_utils.compile_glob``).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool, raise_if_aborted
from pi_agent_core.coding_tools.path_utils import (
    DEFAULT_IGNORE_DIRS,
    compile_glob,
    resolve_to_cwd,
)
from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)
from pi_agent_core.types import AgentTool, AgentToolResult

DEFAULT_FIND_LIMIT = 1000

_DESCRIPTION = (
    "Search for files by glob pattern. Returns matching file paths relative to the search "
    "directory. Skips common ignored directories (.git, node_modules, etc.). Output is "
    f"truncated to {DEFAULT_FIND_LIMIT} results or {DEFAULT_MAX_BYTES // 1024}KB "
    "(whichever is hit first)."
)


class FindParams(BaseModel):
    pattern: str = Field(
        description="Glob pattern to match files, e.g. '*.ts', '**/*.json', or 'src/**/*.spec.ts'"
    )
    path: str | None = Field(
        default=None, description="Directory to search in (default: current directory)"
    )
    limit: int | None = Field(default=None, description="Maximum number of results (default: 1000)")


def _walk_matches(search_path: str, pattern: str, limit: int, is_aborted) -> list[str]:
    """Collect up to *limit* matching relative POSIX paths in walk order.

    Both files and directories are candidates (fd's default). Ignored
    directories are pruned before descent; entries per directory are sorted
    for determinism (fd's parallel walk has no stable order to mirror).
    """
    regex, matches_path = compile_glob(pattern)
    results: list[str] = []
    for root, dirnames, filenames in os.walk(search_path):
        if is_aborted():
            raise RuntimeError("Operation aborted")
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_IGNORE_DIRS)
        rel_root = os.path.relpath(root, search_path).replace(os.sep, "/")
        prefix = "" if rel_root == "." else rel_root + "/"
        for name in sorted([*dirnames, *filenames]):
            relative = prefix + name
            candidate = relative if matches_path else name
            if regex.fullmatch(candidate):
                results.append(relative)
                if len(results) >= limit:
                    return results
    return results


def create_find_tool(cwd: str) -> AgentTool:
    """Build a find tool bound to *cwd*."""

    async def execute(
        _tool_call_id: str,
        params: FindParams,
        signal: Any | None = None,
        _on_update: Any | None = None,
    ) -> AgentToolResult:
        raise_if_aborted(signal)
        search_path = resolve_to_cwd(params.path or ".", cwd)
        effective_limit = params.limit if params.limit is not None else DEFAULT_FIND_LIMIT

        if not await asyncio.to_thread(os.path.exists, search_path):
            raise ValueError(f"Path not found: {search_path}")

        def is_aborted() -> bool:
            return signal is not None and getattr(signal, "aborted", False)

        results = await asyncio.to_thread(
            _walk_matches, search_path, params.pattern, effective_limit, is_aborted
        )
        raise_if_aborted(signal)

        if not results:
            return AgentToolResult(
                content=[{"type": "text", "text": "No files found matching pattern"}],
                details=None,
            )

        raw_output = "\n".join(results)
        # Byte cap only: the result limit already capped the row count.
        truncation = truncate_head(raw_output, max_lines=sys.maxsize)
        output = truncation.content
        details: dict[str, Any] = {}
        notices: list[str] = []
        if len(results) >= effective_limit:
            notices.append(
                f"{effective_limit} results limit reached. "
                f"Use limit={effective_limit * 2} for more, or refine pattern"
            )
            details["resultLimitReached"] = effective_limit
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
            details["truncation"] = truncation.to_dict()
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"

        return AgentToolResult(content=[{"type": "text", "text": output}], details=details or None)

    return CodingTool(
        name="find",
        description=_DESCRIPTION,
        label="find",
        parameters=FindParams,
        execute_fn=execute,
        prompt_snippet="Find files by glob pattern (respects .gitignore)",
    )
